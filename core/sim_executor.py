"""
core/sim_executor.py — Thin wrapper over PyLTSpice SimRunner
=============================================================
Each candidate now has TWO netlists (DC + TRAN).
This executor runs both, detects failures, and returns a paired result.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PyLTSpice import SimRunner, LTspice

logger = logging.getLogger(__name__)


@dataclass
class SimResult:
    """Results for one candidate (DC run + TRAN run merged)."""
    # DC paths
    dc_netlist:  Optional[Path]
    dc_raw:      Optional[Path]
    dc_log:      Optional[Path]
    dc_failed:   bool
    # TRAN paths
    tran_netlist: Optional[Path]
    tran_raw:     Optional[Path]
    tran_log:     Optional[Path]
    tran_failed:  bool
    elapsed_s:    float

    @property
    def sim_failed(self) -> bool:
        return self.dc_failed or self.tran_failed


class SimExecutor:
    """
    Runs DC + TRAN netlists for each candidate.
    Both sims run for every candidate; results are paired.
    """

    def __init__(
        self,
        parallel_sims: int = 2,
        timeout_s: float = 120.0,
        simulator=None,
    ) -> None:
        self.parallel_sims = parallel_sims
        self.timeout_s = timeout_s
        self._simulator = simulator or LTspice

    def run_batch(
        self, netlist_pairs: list[tuple[Path, Path]]
    ) -> list[SimResult]:
        """
        Run all (dc_path, tran_path) pairs.
        Returns one SimResult per pair.
        """
        if not netlist_pairs:
            return []

        t0 = time.monotonic()
        results: list[SimResult] = []

        for dc_path, tran_path in netlist_pairs:
            dc_raw,   dc_log   = self._run_one(dc_path)
            tran_raw, tran_log = self._run_one(tran_path)
            elapsed = time.monotonic() - t0
            results.append(SimResult(
                dc_netlist=dc_path, dc_raw=dc_raw, dc_log=dc_log,
                dc_failed=(dc_raw is None),
                tran_netlist=tran_path, tran_raw=tran_raw, tran_log=tran_log,
                tran_failed=(tran_raw is None),
                elapsed_s=elapsed,
            ))
            if dc_raw is None:
                logger.warning("DC sim FAILED (no .raw): %s", dc_path)
            if tran_raw is None:
                logger.warning("TRAN sim FAILED (no .raw): %s", tran_path)

        return results

    def _run_one(self, netlist: Path) -> tuple[Optional[Path], Optional[Path]]:
        """Run a single netlist and return (raw_path, log_path)."""
        try:
            runner = SimRunner(
                output_folder=str(netlist.parent),
                simulator=self._simulator,
                parallel_sims=1,
            )
            runner.run(str(netlist))
            runner.wait_completion(timeout=self.timeout_s)
        except Exception as exc:
            logger.error("SimRunner error on %s: %s", netlist, exc)
            return None, None

        raw = self._find_file(netlist, [".raw", ".RAW"])
        log = self._find_file(netlist, [".log", ".LOG"])
        return raw, log

    @staticmethod
    def _find_file(netlist: Path, suffixes: list[str]) -> Optional[Path]:
        parent = netlist.parent
        # PyLTSpice appends _1 to the filename
        stems = [netlist.stem, netlist.stem + "_1"]
        for stem in stems:
            for suf in suffixes:
                p = parent / (stem + suf)
                if p.exists():
                    return p
        return None
