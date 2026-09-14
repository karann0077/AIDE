"""
core/dynamic_spec.py — Generic Spec for any circuit
====================================================
Replaces the hardcoded inverter-only Spec with a fully dynamic
dataclass that can represent any circuit's performance targets.

The Circuit Generator LLM populates this at runtime from the user's
plain-English prompt. It is also serializable to/from YAML so the
user can inspect and edit it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml


# ---------------------------------------------------------------------------
# Metric target — one measurable performance goal
# ---------------------------------------------------------------------------
@dataclass
class MetricTarget:
    """A single performance requirement for one measured quantity."""
    name: str                          # matches .meas label, e.g. "tpd_sum"
    source: Literal["dc", "tran", "derived"]  # which sim file produces it
    direction: Literal["minimize", "maximize", "target", "max", "min"]
    target_value: float                # hard limit or ideal value (SI units)
    tolerance: float = 0.05           # ±fraction for "target" direction
    weight: float = 1.0               # relative weight in composite error

    # Derived-metric formula (Python expression, evaluated in Python)
    # e.g. "(abs(tphl_sum) + abs(tplh_sum)) / 2"
    # Uses values from the same metrics dict as variables
    formula: str = ""

    def is_satisfied(self, value: float) -> bool:
        """Return True if this metric meets its target."""
        if math.isnan(value):
            return False
        if self.direction in ("max", "maximize"):
            return value >= self.target_value
        elif self.direction in ("min", "minimize"):
            return value <= self.target_value
        else:  # "target"
            return abs(value - self.target_value) / (self.target_value + 1e-30) <= self.tolerance

    def error(self, value: float) -> float:
        """Normalized scalar error (0 = perfect, higher = worse)."""
        if math.isnan(value):
            return 1e6
        tv = self.target_value
        if self.direction in ("min", "minimize"):
            return self.weight * max(0.0, (value - tv) / (tv + 1e-30))
        elif self.direction in ("max", "maximize"):
            return self.weight * max(0.0, (tv - value) / (tv + 1e-30))
        else:  # target
            return self.weight * ((value - tv) / (tv + 1e-30)) ** 2


# ---------------------------------------------------------------------------
# DynamicSpec — the runtime spec object
# ---------------------------------------------------------------------------
@dataclass
class DynamicSpec:
    """
    Fully generic circuit spec — works for inverter, full adder,
    OTA, ring oscillator, or any other SPICE circuit.
    """
    circuit_name: str
    circuit_description: str
    template_dc: Path
    template_tran: Path

    # Search space
    design_variables: dict[str, dict[str, float]]

    # Performance requirements
    metric_targets: list[MetricTarget]

    # Which .meas names to extract from each log
    meas_dc:   list[str] = field(default_factory=list)
    meas_tran: list[str] = field(default_factory=list)

    # Corner / reliability settings
    corners: dict[str, Any] = field(default_factory=lambda: {
        "temp_c": [-40, 27, 125],
        "vdd_pct": [-10, 0, 10],
        "tolerance_pct": 10,
    })

    # Loop budget
    budget: dict[str, Any] = field(default_factory=lambda: {
        "max_iterations": 40,
        "max_no_improve": 8,
        "parallel_sims": 2,
    })

    # LLM settings (inherited from global config / .env)
    llm: dict[str, Any] = field(default_factory=lambda: {
        "model": "gemini-3.6-flash",
        "api_provider": "google",
        "history_window": 5,
        "temperature": 0.3,
    })

    # Reporting
    reporting: dict[str, Any] = field(default_factory=lambda: {
        "log_file": "run_log.jsonl",
        "report_dir": "reports/",
    })

    # -----------------------------------------------------------------------
    # Convenience properties (for backward compat with orchestrator)
    # -----------------------------------------------------------------------
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

    @property
    def vdd_nominal(self) -> float:
        return self.budget.get("vdd_nominal", 1.8)

    # -----------------------------------------------------------------------
    # Core scoring — works for any set of metrics
    # -----------------------------------------------------------------------
    def score(self, metrics: dict[str, float]) -> tuple[bool, float]:
        """
        Returns (all_targets_met, composite_error_scalar).
        Lower error = better. 0.0 = all targets perfectly hit.
        """
        # Evaluate derived metrics first
        enriched = dict(metrics)
        for mt in self.metric_targets:
            if mt.source == "derived" and mt.formula:
                try:
                    local_vars = {k: v for k, v in enriched.items() if not math.isnan(v)}
                    local_vars["abs"] = abs
                    for k in dir(math):
                        if not k.startswith("_"):
                            local_vars[k] = getattr(math, k)
                    enriched[mt.name] = eval(mt.formula, {"__builtins__": {}}, local_vars)
                except Exception:
                    enriched[mt.name] = float("nan")

        total_error = 0.0
        all_passed = True
        for mt in self.metric_targets:
            val = enriched.get(mt.name, float("nan"))
            total_error += mt.error(val)
            if not mt.is_satisfied(val):
                all_passed = False

        return all_passed, total_error

    def verdict_str(self, metrics: dict[str, float]) -> str:
        """Human-readable pass/fail line."""
        passed, err = self.score(metrics)
        status = "PASS ✓" if passed else "FAIL ✗"
        parts = [f"{status}  err={err:.4f}"]
        for mt in self.metric_targets:
            val = metrics.get(mt.name, float("nan"))
            unit, scale = _unit_for(mt.name)
            parts.append(f"{mt.name}={val*scale:.3g}{unit}(target {mt.target_value*scale:.3g}{unit})")
        return "  ".join(parts)

    # -----------------------------------------------------------------------
    # Serialization
    # -----------------------------------------------------------------------
    def to_yaml(self, path: str | Path) -> None:
        """Save this spec as a human-readable YAML file."""
        data = {
            "circuit": {
                "name": self.circuit_name,
                "description": self.circuit_description,
                "template_dc": str(self.template_dc),
                "template_tran": str(self.template_tran),
            },
            "design_variables": self.design_variables,
            "metric_targets": [
                {
                    "name": mt.name,
                    "source": mt.source,
                    "direction": mt.direction,
                    "target_value": mt.target_value,
                    "tolerance": mt.tolerance,
                    "weight": mt.weight,
                    "formula": mt.formula,
                }
                for mt in self.metric_targets
            ],
            "meas_dc": self.meas_dc,
            "meas_tran": self.meas_tran,
            "corners": self.corners,
            "budget": self.budget,
            "llm": self.llm,
            "reporting": self.reporting,
        }
        Path(path).write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))

    @classmethod
    def from_yaml(cls, path: str | Path) -> "DynamicSpec":
        """Load a DynamicSpec from a YAML file (previously saved by to_yaml)."""
        data = yaml.safe_load(Path(path).read_text())
        c = data["circuit"]
        return cls(
            circuit_name=c["name"],
            circuit_description=c.get("description", ""),
            template_dc=Path(c["template_dc"]),
            template_tran=Path(c["template_tran"]),
            design_variables=data["design_variables"],
            metric_targets=[
                MetricTarget(**mt) for mt in data["metric_targets"]
            ],
            meas_dc=data.get("meas_dc", []),
            meas_tran=data.get("meas_tran", []),
            corners=data.get("corners", {}),
            budget=data.get("budget", {}),
            llm=data.get("llm", {}),
            reporting=data.get("reporting", {}),
        )


# ---------------------------------------------------------------------------
# Unit formatting helper
# ---------------------------------------------------------------------------
def _unit_for(name: str) -> tuple[str, float]:
    """Return (unit_string, scale_factor) based on metric name hints."""
    nl = name.lower()
    if any(x in nl for x in ("tpd", "tphl", "tplh", "tco", "delay", "rise", "fall")):
        return "ns", 1e9
    if any(x in nl for x in ("freq", "fclk", "fosc")):
        return "MHz", 1e-6
    if any(x in nl for x in ("power", "pwr", "pd")):
        return "µW", 1e6
    if any(x in nl for x in ("vm", "vout", "vdd", "volt", "swing")):
        return "V", 1.0
    if any(x in nl for x in ("gain", "av")):
        return "dB", 1.0
    if any(x in nl for x in ("current", "idd", "iss")):
        return "µA", 1e6
    return "", 1.0
