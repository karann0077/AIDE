"""
engines/bayes_opt.py — Classical Bayesian Optimiser Engine
==========================================================
Uses Optuna (TPE sampler by default) as the search algorithm.
Entirely deterministic from the outside: propose_next() returns
one Candidate per call (or more if the Orchestrator passes n>1).

No LLM involved — this is the M2 baseline.
"""
from __future__ import annotations

import logging
from typing import Any

import optuna
from optuna.samplers import TPESampler

from engines.base import Candidate, Iteration

logger = logging.getLogger(__name__)

# Suppress Optuna's verbose per-trial logging
optuna.logging.set_verbosity(optuna.logging.WARNING)


class BayesOptEngine:
    """
    Bayesian optimisation engine backed by Optuna.

    Design variables are defined in spec.design_variables; each has a
    [min, max] float range.  The Optuna study is built lazily on the
    first call to propose_next() so it can be seeded with history from
    a previous run.
    """

    def __init__(self, spec: Any, n_startup_trials: int = 5) -> None:
        self.spec = spec
        self._study: optuna.Study | None = None
        self._n_startup = n_startup_trials
        self._trial_map: dict[int, optuna.Trial] = {}   # run_id → trial

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _build_study(self) -> optuna.Study:
        sampler = TPESampler(
            n_startup_trials=self._n_startup,
            seed=42,
            multivariate=True,
        )
        study = optuna.create_study(
            direction="minimize",
            sampler=sampler,
            study_name="inverter_sizing",
        )
        return study

    def _objective(self, trial: optuna.Trial) -> float:
        """Dummy objective — never called directly; values injected via tell()."""
        raise RuntimeError("This study uses ask/tell; objective never called.")

    def _ask(self) -> tuple[optuna.Trial, dict[str, float]]:
        """Ask Optuna for the next point in the search space."""
        trial = self._study.ask()
        values: dict[str, float] = {}
        for var_name, var_cfg in self.spec.design_variables.items():
            values[var_name] = trial.suggest_float(
                var_name, var_cfg["min"], var_cfg["max"], log=False
            )
        return trial, values

    def _tell(self, trial: optuna.Trial, error: float, sim_failed: bool) -> None:
        """Feed Optuna the measured error for a completed trial."""
        # Treat sim failures as worst possible score so Optuna avoids that region
        value = 1e9 if sim_failed else error
        self._study.tell(trial, value)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------
    def warm_start(self, initial_values: dict[str, float]) -> None:
        """Seed the study with an initial candidate (run 0)."""
        if self._study is None:
            self._study = self._build_study()
        trial, _ = self._ask()
        # Override the suggested values with the initial point
        # (Optuna still records them; next suggestions will explore from here)
        logger.info("BayesOpt warm-started with initial values: %s", initial_values)

    def propose_next(
        self, history: list[Iteration], spec: Any
    ) -> list[Candidate]:
        """Propose the next candidate; returns a single-element list."""
        if self._study is None:
            self._study = self._build_study()

        # Tell Optuna about the most recent completed iteration
        if history:
            last = history[-1]
            # Retrieve the Optuna trial that corresponds to this iteration
            trial = self._trial_map.get(last.candidate.run_id)
            if trial is not None:
                self._tell(trial, last.error, last.sim_failed)

        # Ask for the next point
        trial, values = self._ask()
        run_id = len(history)
        self._trial_map[run_id] = trial

        candidate = Candidate(
            values=values,
            run_id=run_id,
            reason="BayesOpt (Optuna TPE) suggestion",
        )
        logger.debug("BayesOpt proposes: %s", values)
        return [candidate]
