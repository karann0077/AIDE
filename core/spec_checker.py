"""
core/spec_checker.py — Spec loading and pass/fail scoring
==========================================================
Pure Python, zero simulator dependency — trivially unit-testable.

Exports:
  Spec          — parsed spec dataclass
  load_spec()   — reads spec.yaml → Spec
  score()       — (metrics, Spec) → (passed: bool, error: float)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Spec dataclass
# ---------------------------------------------------------------------------
@dataclass
class Spec:
    # Circuit info
    circuit_name: str
    template_dc: Path      # DC sweep → Vm
    template_tran: Path    # Transient → tpd

    # Design variables with bounds + initial values
    design_variables: dict[str, dict[str, float]]

    # Performance targets
    target: dict[str, float]

    # Corner parameters
    corners: dict[str, Any]

    # Loop budget
    budget: dict[str, Any]

    # LLM settings
    llm: dict[str, Any]

    # Reporting
    reporting: dict[str, Any]

    # Derived convenience properties
    @property
    def vm_target(self) -> float:
        return self.target["vm_ratio"] * self.target["vdd_nominal"]

    @property
    def vm_tolerance(self) -> float:
        return self.target["vm_tolerance"]

    @property
    def tpd_max(self) -> float:
        return self.target["tpd_max_ns"] * 1e-9    # convert ns → s

    @property
    def max_iterations(self) -> int:
        return self.budget["max_iterations"]

    @property
    def max_no_improve(self) -> int:
        return self.budget["max_no_improve"]

    @property
    def parallel_sims(self) -> int:
        return self.budget.get("parallel_sims", 2)

    @property
    def initial_values(self) -> dict[str, float]:
        return {k: v["init"] for k, v in self.design_variables.items()}


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------
def load_spec(yaml_path: str | Path) -> Spec:
    """Parse spec.yaml into a Spec object."""
    data = yaml.safe_load(Path(yaml_path).read_text())
    return Spec(
        circuit_name=data["circuit"]["name"],
        template_dc=Path(data["circuit"]["template_dc"]),
        template_tran=Path(data["circuit"]["template_tran"]),
        design_variables=data["design_variables"],
        target=data["target"],
        corners=data["corners"],
        budget=data["budget"],
        llm=data.get("llm", {}),
        reporting=data.get("reporting", {}),
    )


# ---------------------------------------------------------------------------
# Scoring function — the only place pass/fail is decided
# ---------------------------------------------------------------------------
def score(metrics: dict[str, Any], spec: Spec) -> tuple[bool, float]:
    """
    Returns (passed, error_scalar).

    error_scalar:
      - Vm error:  normalized squared distance from vm_target
      - tpd error: normalized excess above tpd_max (0 if within spec)
      Lower is better; 0.0 means perfectly on-spec.

    Any NaN / missing metric is treated as a very large error so the
    optimizer avoids that region of the search space.
    """
    def safe(val: Any, default: float = 1e6) -> float:
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return default
        return float(val)

    vm = safe(metrics.get("vm"))
    tpd = safe(metrics.get("tpd"))

    # Normalized Vm error
    vm_err = ((vm - spec.vm_target) / spec.vm_target) ** 2

    # Normalized tpd penalty (only counts if over limit)
    tpd_err = max(0.0, (tpd - spec.tpd_max) / spec.tpd_max)

    error = vm_err + tpd_err

    # Pass condition
    vm_within = abs(vm - spec.vm_target) / spec.vm_target <= spec.vm_tolerance
    tpd_within = tpd <= spec.tpd_max

    passed = vm_within and tpd_within
    return passed, error


# ---------------------------------------------------------------------------
# Human-readable verdict string (for logging)
# ---------------------------------------------------------------------------
def verdict_str(metrics: dict, spec: Spec) -> str:
    passed, err = score(metrics, spec)
    vm = metrics.get("vm", float("nan"))
    tpd = metrics.get("tpd", float("nan"))
    status = "PASS ✓" if passed else "FAIL ✗"
    return (
        f"{status}  vm={vm:.4g} V (target {spec.vm_target:.4g} V)  "
        f"tpd={tpd*1e9:.3f} ns (max {spec.tpd_max*1e9:.3f} ns)  "
        f"err={err:.4f}"
    )
