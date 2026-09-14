"""
engines/__init__.py — Engine factory
"""
from __future__ import annotations
from engines.base import Candidate, DecisionEngine, Iteration
from engines.bayes_opt import BayesOptEngine
from engines.llm_agent import LLMAgentEngine

__all__ = [
    "Candidate",
    "DecisionEngine",
    "Iteration",
    "BayesOptEngine",
    "LLMAgentEngine",
]


def get_engine(name: str, spec: "Any") -> "DecisionEngine":
    """Factory: 'bayes' → BayesOptEngine, 'llm' → LLMAgentEngine."""
    from typing import Any
    if name == "bayes":
        return BayesOptEngine(spec)
    elif name == "llm":
        return LLMAgentEngine(spec)
    else:
        raise ValueError(f"Unknown engine '{name}'. Choose 'bayes' or 'llm'.")
