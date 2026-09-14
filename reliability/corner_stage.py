"""
reliability/corner_stage.py — Outer Reliability Loop
=====================================================
Runs Monte Carlo and worst-case corner simulations on the winning
nominal design, then decides whether to:
  (a) declare success  — pass
  (b) tighten targets and re-enter the inner loop  — fail

Uses PyLTSpice's Analysis Toolkit for automatic Monte Carlo netlist
generation and LTSteps for parsing stepped results.

Workflow:
  1. Generate MC netlist (device tolerances from spec.corners).
  2. Generate corner netlist (temp × VDD sweep).
  3. Run both through SimRunner.
  4. Check sigma margin on each metric.
  5. Return CornerResult(passed, tightened_target).
"""
from __future__ import annotations

import logging
import math
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PyLTSpice import SimRunner, LTspice
from PyLTSpice import SpiceEditor

from core.spec_checker import Spec, score

logger = logging.getLogger(__name__)

# Number of Monte Carlo runs
MC_RUNS = 200
# Minimum sigma margin required on each metric
SIGMA_MARGIN = 3.0


# ---------------------------------------------------------------------------
# Monte Carlo Model Parameters
# ---------------------------------------------------------------------------
_MC_MODELS = """\
.param VTH_NOM_N=0.5
.param VTH_NOM_P=-0.5
.param VTH_N_MC={VTH_NOM_N * (1+mc(mc_run, {tol}))}
.param VTH_P_MC={VTH_NOM_P * (1+mc(mc_run, {tol}))}

.model NMOS_MC NMOS (LEVEL=3 TOX=4e-9 VTO={VTH_N_MC} UO=450 THETA=0.1
+ KAPPA=0.3 ETA=0.01 NSUB=1e17 LD=5n WD=5n)
.model PMOS_MC PMOS (LEVEL=3 TOX=4e-9 VTO={VTH_P_MC} UO=150 THETA=0.1
+ KAPPA=0.3 ETA=0.01 NSUB=1e17 LD=5n WD=5n)
"""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass
class CornerResult:
    """Outcome of the reliability stage."""
    passed: bool
    # If failed, suggest a tightened vm_ratio target for the inner loop
    suggested_vm_ratio: float | None = None
    suggested_tpd_max_ns: float | None = None
    # MC stats
    mc_vm_mean: float = float("nan")
    mc_vm_std: float = float("nan")
    mc_tpd_mean: float = float("nan")
    mc_tpd_std: float = float("nan")
    # MC Samples
    mc_vm_samples: list[float] = field(default_factory=list)
    mc_tpd_samples: list[float] = field(default_factory=list)
    # Corner stats
    corner_worst_vm: float = float("nan")
    corner_worst_tpd: float = float("nan")
    details: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------
class CornerStage:
    """
    Runs Monte Carlo + worst-case corners on the winning sizing.

    Args:
        spec:       Parsed spec.
        sim_root:   Directory for intermediate simulation files.
        simulator:  PyLTSpice simulator class (defaults to LTspice).
    """

    def __init__(
        self,
        spec: Spec,
        sim_root: str | Path = "simulations/reliability",
        simulator=None,
    ) -> None:
        self.spec = spec
        self.sim_root = Path(sim_root)
        self.sim_root.mkdir(parents=True, exist_ok=True)
        self._simulator = simulator or LTspice

    # ------------------------------------------------------------------
    def run(self, winning_dc_netlist: Path, winning_tran_netlist: Path) -> CornerResult:
        """
        Run reliability suite on the winning DC and TRAN netlists.
        Returns metrics and PASS/FAIL boolean.
        """
        logger.info("Starting Monte Carlo and PVT Corners (N=%d)...", MC_RUNS)
        mc_metrics = self._run_monte_carlo(winning_tran_netlist)
        corner_metrics = self._run_corners(winning_dc_netlist, winning_tran_netlist)

        return self._analyse(mc_metrics, corner_metrics)

    # ------------------------------------------------------------------
    def _run_monte_carlo(self, tran_netlist: Path) -> list[dict[str, float]]:
        """
        Generate and run a Monte Carlo netlist.
        Returns a list of per-run metric dicts.
        """
        mc_dir = self.sim_root / "mc"
        mc_dir.mkdir(parents=True, exist_ok=True)
        mc_net = mc_dir / f"{self.spec.circuit_name}_mc.net"
        shutil.copy2(tran_netlist, mc_net)

        tol = self.spec.corners.get("tolerance_pct", 10) / 100.0

        # Add Monte Carlo SPICE directives
        spice_ed = SpiceEditor(str(mc_net))
        spice_ed.add_instructions(
            f".step param mc_run 1 {MC_RUNS} 1",
            _MC_MODELS.format(tol=tol),
        )
        spice_ed.save_netlist(str(mc_net))

        # We need to change the models in the netlist from NMOS to NMOS_MC and PMOS to PMOS_MC
        text = mc_net.read_text()
        text = text.replace("NMOS W=", "NMOS_MC W=").replace("PMOS W=", "PMOS_MC W=")
        mc_net.write_text(text)

        runner = SimRunner(
            output_folder=str(mc_dir),
            simulator=self._simulator,
        )
        runner.run(str(mc_net))
        runner.wait_completion(timeout=600)

        # Parse stepped results
        return self._parse_stepped(mc_dir / f"{self.spec.circuit_name}_mc.log")

    # ------------------------------------------------------------------
    def _run_corners(self, dc_netlist: Path, tran_netlist: Path) -> list[dict[str, float]]:
        """
        Run worst-case corners: cross of temp × VDD.
        """
        corner_results: list[dict[str, float]] = []
        temps = self.spec.corners.get("temp_c", [27])
        vdd_pcts = self.spec.corners.get("vdd_pct", [0])
        vdd_nom = self.spec.target["vdd_nominal"]

        for temp in temps:
            for vdd_pct in vdd_pcts:
                vdd = vdd_nom * (1 + vdd_pct / 100.0)
                cname = f"T{temp:+d}_V{vdd_pct:+d}"
                c_dir = self.sim_root / "corners" / cname
                c_dir.mkdir(parents=True, exist_ok=True)
                
                c_dc_net = c_dir / f"{self.spec.circuit_name}_corner_dc.net"
                c_tran_net = c_dir / f"{self.spec.circuit_name}_corner_tran.net"
                shutil.copy2(dc_netlist, c_dc_net)
                shutil.copy2(tran_netlist, c_tran_net)

                # Update VDD and Temp in both
                for net in (c_dc_net, c_tran_net):
                    spice_ed = SpiceEditor(str(net))
                    spice_ed.set_parameters(temp=str(temp), vdd=str(vdd))
                    try:
                        spice_ed.set_component_value("VDD", str(vdd))
                    except Exception:
                        pass
                    spice_ed.save_netlist(str(net))

                runner = SimRunner(
                    output_folder=str(c_dir),
                    simulator=self._simulator,
                )
                runner.run(str(c_dc_net))
                runner.run(str(c_tran_net))
                runner.wait_completion(timeout=120)

                log_dc = c_dir / f"{self.spec.circuit_name}_corner_dc.log"
                log_tran = c_dir / f"{self.spec.circuit_name}_corner_tran.log"
                
                from core.result_parser import extract_metrics
                metrics = extract_metrics(dc_log=log_dc, tran_log=log_tran)
                metrics["corner"] = cname
                corner_results.append(metrics)
                logger.info(
                    "Corner %s: vm=%.4g V, tpd=%.3g ns",
                    cname, metrics.get("vm", math.nan),
                    metrics.get("tpd", math.nan) * 1e9 if not math.isnan(metrics.get("tpd", math.nan)) else math.nan,
                )

        return corner_results

    # ------------------------------------------------------------------
    def _parse_stepped(self, log_path: Path) -> list[dict[str, float]]:
        """Parse a stepped simulation log (Monte Carlo results)."""
        from core.result_parser import _MEAS_RE
        results: list[dict[str, float]] = []
        if not log_path.exists():
            logger.warning("MC log not found: %s", log_path)
            return results

        try:
            from PyLTSpice import LTSteps  # type: ignore
            steps = LTSteps(str(log_path))
            for step in range(steps.get_step_count()):
                d: dict[str, float] = {}
                for meas in ("vm", "tpd", "tphl", "tplh"):
                    try:
                        d[meas] = float(steps.get_measure_value(meas, step))
                    except Exception:
                        d[meas] = float("nan")
                results.append(d)
        except Exception as exc:
            logger.debug("LTSteps parse failed (%s); using regex.", exc)
            # Regex fallback — crude but works
            text = log_path.read_text(errors="replace")
            for m in _MEAS_RE.finditer(text):
                name = m.group("name").lower()
                if name in ("vm", "tpd"):
                    results.append({name: float(m.group("value"))})

        logger.info("MC: parsed %d stepped results", len(results))
        return results

    # ------------------------------------------------------------------
    def _analyse(
        self,
        mc_metrics: list[dict],
        corner_metrics: list[dict],
    ) -> CornerResult:
        """Compute sigma margins and decide pass/fail."""

        # Monte Carlo statistics
        vms = [m.get("vm", math.nan) for m in mc_metrics if not math.isnan(m.get("vm", math.nan))]
        tpds = [m.get("tpd", math.nan) for m in mc_metrics if not math.isnan(m.get("tpd", math.nan))]

        mc_vm_mean = _mean(vms) if vms else math.nan
        mc_vm_std = _std(vms) if vms else math.nan
        mc_tpd_mean = _mean(tpds) if tpds else math.nan
        mc_tpd_std = _std(tpds) if tpds else math.nan

        vm_target = self.spec.vm_target
        tpd_max = self.spec.tpd_max

        if not corner_metrics:
            return CornerResult(
                passed=False,
                details={"reason": "no_corner_results"},
            )

        valid_corners = []
        for metric in corner_metrics:
            vm = metric.get("vm")
            tpd = metric.get("tpd")
            if vm is not None and math.isfinite(vm) and tpd is not None and math.isfinite(tpd):
                valid_corners.append(metric)
                
        if len(valid_corners) != len(corner_metrics):
            return CornerResult(
                passed=False,
                details={"reason": "invalid_corner_measurements"},
            )

        # Corner worst-case
        corner_worst_vm = max(
            (abs(float(m.get("vm", float("nan"))) - vm_target) for m in corner_metrics if math.isfinite(float(m.get("vm", float("nan"))))), 
            default=float("inf"),
        )
        corner_worst_tpd = max(
            (m.get("tpd", 0.0) for m in corner_metrics), default=0.0
        )

        def _valid_sigma_margin(mean: float, std: float, limit: float, is_target: bool = False) -> tuple[bool, float]:
            if not math.isfinite(mean) or not math.isfinite(std) or std <= 0:
                return False, float("nan")
            if is_target:
                distance = abs(mean - limit)
                allowance = self.spec.target["vm_tolerance"] * limit
                margin = (allowance - distance) / std
            else:
                margin = (limit - mean) / std
            return math.isfinite(margin), margin
            
        vm_sigma_ok, vm_sigma = _valid_sigma_margin(mc_vm_mean, mc_vm_std, vm_target, is_target=True)
        tpd_sigma_ok, tpd_sigma = _valid_sigma_margin(mc_tpd_mean, mc_tpd_std, tpd_max)
        
        mc_vm_ok = vm_sigma_ok and vm_sigma >= SIGMA_MARGIN
        mc_tpd_ok = tpd_sigma_ok and tpd_sigma >= SIGMA_MARGIN
        mc_ok = mc_vm_ok and mc_tpd_ok

        logger.info(
            "MC  vm: mean=%.4g std=%.4g  σ_margin=%.2f",
            mc_vm_mean, mc_vm_std, vm_sigma,
        )
        logger.info(
            "MC tpd: mean=%.4g std=%.4g  σ_margin=%.2f",
            mc_tpd_mean, mc_tpd_std, tpd_sigma,
        )
        logger.info(
            "Corner worst: Δvm=%.4g, tpd=%.4g ns",
            corner_worst_vm, corner_worst_tpd * 1e9,
        )

        corner_ok = (
            corner_worst_vm <= self.spec.target["vm_tolerance"] * self.spec.vm_target
            and corner_worst_tpd <= tpd_max
        )
        mc_ok = (vm_sigma >= SIGMA_MARGIN or math.isinf(vm_sigma)) and \
                (tpd_sigma >= SIGMA_MARGIN or math.isinf(tpd_sigma))

        passed = corner_ok and mc_ok

        # If failed, suggest tighter targets for re-entry
        suggested_vm_ratio = None
        suggested_tpd_max_ns = None
        if not passed:
            if not mc_ok and not math.isnan(mc_vm_std):
                # Push Vm target tighter toward true center
                shift = SIGMA_MARGIN * mc_vm_std
                new_vm = vm_target - (mc_vm_mean - vm_target)
                suggested_vm_ratio = new_vm / self.spec.target["vdd_nominal"]
                logger.info(
                    "Tightened vm_ratio suggestion: %.4f (was %.4f)",
                    suggested_vm_ratio,
                    self.spec.target["vm_ratio"],
                )
            if not math.isnan(mc_tpd_std):
                new_tpd_max = tpd_max - SIGMA_MARGIN * mc_tpd_std
                if math.isfinite(new_tpd_max):
                    new_tpd_max = max(new_tpd_max, 1e-15)
                else:
                    new_tpd_max = None
                suggested_tpd_max_ns = (new_tpd_max * 1e9 if new_tpd_max is not None else None)
                
                logger.info(
                    "Tightened tpd_max suggestion: %s ns (was %.4f ns)",
                    f"{suggested_tpd_max_ns:.4f}" if suggested_tpd_max_ns else "None",
                    tpd_max * 1e9,
                )

        # Compute yield
        total_mc = len(vms) if vms else 0
        mc_yield_pct = 0.0
        if total_mc > 0:
            passes = 0
            allowance = self.spec.target["vm_tolerance"] * vm_target
            for vm_s, tpd_s in zip(vms, tpds):
                if abs(vm_s - vm_target) <= allowance and tpd_s <= tpd_max:
                    passes += 1
            mc_yield_pct = (passes / total_mc) * 100.0

        return CornerResult(
            passed=passed,
            suggested_vm_ratio=suggested_vm_ratio,
            suggested_tpd_max_ns=suggested_tpd_max_ns,
            mc_vm_mean=mc_vm_mean,
            mc_vm_std=mc_vm_std,
            mc_tpd_mean=mc_tpd_mean,
            mc_tpd_std=mc_tpd_std,
            mc_vm_samples=vms,
            mc_tpd_samples=tpds,
            corner_worst_vm=corner_worst_vm,
            corner_worst_tpd=corner_worst_tpd,
            details={
                "vm_sigma": vm_sigma,
                "tpd_sigma": tpd_sigma,
                "corner_ok": corner_ok,
                "mc_ok": mc_ok,
                "mc_yield_pct": mc_yield_pct,
            },
        )


# ---------------------------------------------------------------------------
# Simple statistics helpers (avoid numpy dependency for reliability module)
# ---------------------------------------------------------------------------
def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else math.nan

def _std(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
