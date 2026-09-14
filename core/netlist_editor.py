"""
core/netlist_editor.py — Thin wrapper over PyLTSpice SpiceEditor
=================================================================
LTspice on Mac cannot mix .DC and .TRAN in a single netlist, so we
maintain TWO template files:
  inverter_dc.cir   → DC sweep → reads Vm
  inverter_tran.cir → Transient → reads tphl, tplh

Both are written with the same .param values each iteration.
SimExecutor runs them in parallel and ResultParser merges the logs.

Folder layout (created automatically):
  simulations/
    iter_<N>/
      cand_<M>/
        dc/    ← DC netlist + results
        tran/  ← Transient netlist + results
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from PyLTSpice import SpiceEditor

from engines.base import Candidate
from core.spec_checker import Spec

logger = logging.getLogger(__name__)


class NetlistEditor:
    """
    Manages per-iteration netlist copies.

    Usage:
        editor = NetlistEditor(spec, sim_root="simulations")
        dc_path, tran_path = editor.write(candidate, iteration=3, candidate_idx=0)
    """

    def __init__(self, spec: Spec, sim_root: str | Path = "simulations") -> None:
        self.spec = spec
        self.sim_root = Path(sim_root)
        # Resolve templates relative to project root (spec.yaml location)
        self._dc_tpl   = spec.template_dc
        self._tran_tpl = spec.template_tran

    def write(
        self,
        candidate: Candidate,
        iteration: int,
        candidate_idx: int = 0,
    ) -> tuple[Path, Path]:
        """
        Write modified DC and TRAN netlists for this candidate.

        Returns: (dc_netlist_path, tran_netlist_path)
        """
        base = self.sim_root / f"iter_{iteration:03d}" / f"cand_{candidate_idx:02d}"
        dc_dir   = base / "dc"
        tran_dir = base / "tran"
        dc_dir.mkdir(parents=True, exist_ok=True)
        tran_dir.mkdir(parents=True, exist_ok=True)

        spice_params = candidate.to_spice_params()

        dc_path   = self._write_one(self._dc_tpl,   dc_dir   / "inverter_dc.cir",   spice_params)
        tran_path = self._write_one(self._tran_tpl,  tran_dir / "inverter_tran.cir", spice_params)

        logger.debug(
            "iter=%d cand=%d  DC→%s  TRAN→%s  params=%s",
            iteration, candidate_idx, dc_path, tran_path, spice_params,
        )
        return dc_path, tran_path

    def _write_one(self, template: Path, dest: Path, params: dict[str, str]) -> Path:
        shutil.copy2(template, dest)
        netlist = SpiceEditor(str(dest))
        netlist.set_parameters(**params)
        netlist.save_netlist(str(dest))
        return dest

    def write_batch(
        self,
        candidates: list[Candidate],
        iteration: int,
    ) -> list[tuple[Path, Path]]:
        """Write DC + TRAN netlists for every candidate in the batch."""
        return [
            self.write(c, iteration, idx) for idx, c in enumerate(candidates)
        ]
