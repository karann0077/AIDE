"""
engines/base.py — Decision Engine protocol
==========================================
Any optimizer or LLM agent implements this Protocol so they are
drop-in swappable behind the Orchestrator.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Data model: one candidate set of component values
# ---------------------------------------------------------------------------
@dataclass
class Candidate:
    """A proposed sizing for one simulation run."""
    values: dict[str, float]          # e.g. {"mn1_w": 2e-6, "mp1_w": 4e-6, ...}
    run_id: int = 0                    # filled by orchestrator
    # Optional: LLM may attach a human-readable reason
    reason: str = ""

    def to_spice_params(self) -> dict[str, str]:
        """Format float values as SPICE-friendly strings (e.g. 2e-6 → '2u')."""
        out = {}
        for k, v in self.values.items():
            if v >= 1e-3:
                out[k] = f"{v*1e3:.4g}m"
            elif v >= 1e-6:
                out[k] = f"{v*1e6:.4g}u"
            elif v >= 1e-9:
                out[k] = f"{v*1e9:.4g}n"
            elif v >= 1e-12:
                out[k] = f"{v*1e12:.4g}p"
            else:
                out[k] = f"{v:.6g}"
        return out


# ---------------------------------------------------------------------------
# Data model: one completed iteration
# ---------------------------------------------------------------------------
@dataclass
class Iteration:
    """A completed simulation run — candidate + measured metrics + score."""
    iteration: int
    candidate: Candidate
    metrics: dict[str, float]         # raw .meas results
    error: float                      # scalar cost (lower = better)
    passed: bool                      # True if nominal spec fully met
    sim_failed: bool = False          # True if LTspice itself crashed/diverged
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# The Protocol — any engine implements exactly this one method
# ---------------------------------------------------------------------------
@runtime_checkable
class DecisionEngine(Protocol):
    """
    Propose the next batch of candidates to evaluate.

    Args:
        history: All iterations completed so far (oldest first).
        spec:    The parsed spec object (see core/spec_checker.py).

    Returns:
        A list of Candidate objects (one for single-point engines,
        possibly several for population-based or batched LLM engines).
    """
    def propose_next(self, history: list[Iteration], spec: Any) -> list[Candidate]: ...

    def warm_start(self, initial_values: dict[str, float]) -> None:
        """Optional: seed the engine with a known starting point."""
        ...
