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
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass
class CornerResult:
    """Outcome of the reliability stage."""
    passed: bool
    # If failed, suggest a tightened vm_ratio target for the inner loop
    suggested_vm_ratio: Optional[float] = None
    suggested_tpd_max_ns: Optional[float] = None
    mc_vm_mean: float = float("nan")
    mc_vm_std: float = float("nan")
    mc_tpd_mean: float = float("nan")
    mc_tpd_std: float = float("nan")
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
    def run(self, winning_netlist: Path) -> CornerResult:
        """
        Full reliability check.  Returns a CornerResult.
        """
        logger.info("=== Reliability Stage: starting ===")

        mc_metrics = self._run_monte_carlo(winning_netlist)
        corner_metrics = self._run_corners(winning_netlist)

        return self._analyse(mc_metrics, corner_metrics)

    # ------------------------------------------------------------------
    def _run_monte_carlo(self, netlist: Path) -> list[dict[str, float]]:
        """
        Generate and run a Monte Carlo netlist.
        Returns a list of per-run metric dicts.
        """
        mc_dir = self.sim_root / "monte_carlo"
        mc_dir.mkdir(parents=True, exist_ok=True)
        mc_net = mc_dir / "inverter_mc.net"
        shutil.copy2(netlist, mc_net)

        tol = self.spec.corners.get("tolerance_pct", 10) / 100.0

        # Add Monte Carlo SPICE directives
        spice_ed = SpiceEditor(str(mc_net))
        spice_ed.add_instructions(
            f".step param mc_run 1 {MC_RUNS} 1",
            f"* tolerance applied via model parameter variation",
            f".param vth0_n_var='0.5*(1+mc(mc_run,{tol}))'",
            f".param vth0_p_var='-0.5*(1+mc(mc_run,{tol}))'",
        )
        spice_ed.save_netlist(str(mc_net))

        runner = SimRunner(
            output_folder=str(mc_dir),
            simulator=self._simulator,
        )
        runner.run(str(mc_net))
        runner.wait_completion(timeout=600)

        # Parse stepped results
        return self._parse_stepped(mc_dir / "inverter_mc.log")

    # ------------------------------------------------------------------
    def _run_corners(self, netlist: Path) -> list[dict[str, float]]:
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
                c_net = c_dir / "inverter_corner.net"
                shutil.copy2(netlist, c_net)

                spice_ed = SpiceEditor(str(c_net))
                spice_ed.set_parameters(temp=str(temp), vdd=str(vdd))
                # Update VDD source value in the netlist
                try:
                    spice_ed.set_component_value("VDD", str(vdd))
                except Exception:
                    pass  # some netlists use .param vdd
                spice_ed.save_netlist(str(c_net))

                runner = SimRunner(
                    output_folder=str(c_dir),
                    simulator=self._simulator,
                )
                runner.run(str(c_net))
                runner.wait_completion(timeout=120)

                log_p = c_dir / "inverter_corner.log"
                from core.result_parser import parse_log
                metrics = parse_log(log_p)
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

        # Corner worst-case
        corner_worst_vm = min(
            (abs(m.get("vm", vm_target) - vm_target) for m in corner_metrics), default=0.0
        )
        corner_worst_tpd = max(
            (m.get("tpd", 0.0) for m in corner_metrics), default=0.0
        )

        # Sigma check for MC
        if not math.isnan(mc_vm_mean) and mc_vm_std > 0:
            vm_sigma = abs(mc_vm_mean - vm_target) / mc_vm_std
        else:
            vm_sigma = math.inf

        if not math.isnan(mc_tpd_mean) and mc_tpd_std > 0:
            tpd_sigma = (tpd_max - mc_tpd_mean) / mc_tpd_std
        else:
            tpd_sigma = math.inf

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
                suggested_tpd_max_ns = (tpd_max - SIGMA_MARGIN * mc_tpd_std) * 1e9
                logger.info(
                    "Tightened tpd_max suggestion: %.4f ns (was %.4f ns)",
                    suggested_tpd_max_ns,
                    tpd_max * 1e9,
                )

        return CornerResult(
            passed=passed,
            suggested_vm_ratio=suggested_vm_ratio,
            suggested_tpd_max_ns=suggested_tpd_max_ns,
            mc_vm_mean=mc_vm_mean,
            mc_vm_std=mc_vm_std,
            mc_tpd_mean=mc_tpd_mean,
            mc_tpd_std=mc_tpd_std,
            corner_worst_vm=corner_worst_vm,
            corner_worst_tpd=corner_worst_tpd,
            details={
                "vm_sigma": vm_sigma,
                "tpd_sigma": tpd_sigma,
                "corner_ok": corner_ok,
                "mc_ok": mc_ok,
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
