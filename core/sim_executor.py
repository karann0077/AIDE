"""
core/sim_executor.py — Thin wrapper over PyLTSpice SimRunner
=============================================================
Each candidate now has TWO netlists (DC + TRAN).
This executor runs both, detects failures, and returns a paired result.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PyLTSpice import LTspice

logger = logging.getLogger(__name__)

@dataclass
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    elapsed_s: float
    raw_path: Optional[Path] = None
    log_path: Optional[Path] = None


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
        Run all (dc_path, tran_path) pairs in parallel.
        Returns one SimResult per pair.
        """
        if not netlist_pairs:
            return []

        results: list[SimResult] = []

        with ThreadPoolExecutor(max_workers=self.parallel_sims) as pool:
            futures = {
                pool.submit(self._run_pair, dc_path, tran_path): (dc_path, tran_path)
                for dc_path, tran_path in netlist_pairs
            }
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:
                    logger.error("Simulation pair failed: %s", exc)
                    
        return results

    def _run_pair(self, dc_path: Path, tran_path: Path) -> SimResult:
        candidate_t0 = time.monotonic()
        dc_res = self._run_one(dc_path)
        tran_res = self._run_one(tran_path)
        elapsed = time.monotonic() - candidate_t0
        
        sim_result = SimResult(
            dc_netlist=dc_path, dc_raw=dc_res.raw_path, dc_log=dc_res.log_path,
            dc_failed=(dc_res.raw_path is None or dc_res.returncode != 0),
            tran_netlist=tran_path, tran_raw=tran_res.raw_path, tran_log=tran_res.log_path,
            tran_failed=(tran_res.raw_path is None or tran_res.returncode != 0),
            elapsed_s=elapsed,
        )
        if sim_result.dc_failed:
            logger.warning("DC sim FAILED (code=%d, no .raw): %s\nStderr: %s", 
                           dc_res.returncode, dc_path, dc_res.stderr)
        if sim_result.tran_failed:
            logger.warning("TRAN sim FAILED (code=%d, no .raw): %s\nStderr: %s", 
                           tran_res.returncode, tran_path, tran_res.stderr)
        return sim_result

    def _run_one(self, netlist: Path) -> ProcessResult:
        """Run a single netlist using subprocess and return execution details."""
        t0 = time.monotonic()
        exe = LTspice.spice_exe[0] if isinstance(LTspice.spice_exe, list) and LTspice.spice_exe else LTspice.spice_exe
        
        cmd = [exe, "-b", "-Run", str(netlist.resolve())] if sys.platform == "win32" else [exe, "-b", str(netlist.resolve())]

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout_s
            )
            returncode = proc.returncode
            stdout = proc.stdout
            stderr = proc.stderr
        except subprocess.TimeoutExpired as exc:
            logger.error("LTspice timed out: %s", netlist)
            return ProcessResult(-1, "", str(exc), time.monotonic() - t0)
        except Exception as exc:
            logger.error("Failed to run LTspice: %s", exc)
            return ProcessResult(-1, "", str(exc), time.monotonic() - t0)

        elapsed = time.monotonic() - t0
        raw = self._find_file(netlist, [".raw", ".RAW"])
        log = self._find_file(netlist, [".log", ".LOG"])
        
        return ProcessResult(returncode, stdout, stderr, elapsed, raw, log)

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
