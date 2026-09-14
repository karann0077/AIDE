"""
m1_manual_harness.py — M1: Plumbing Test (No Optimizer, No LLM)
================================================================
Single-shot script to verify the full pipeline:
  Edit netlists → Run LTspice (DC + TRAN) → Parse logs → Print results

Usage:
    python m1_manual_harness.py
    python m1_manual_harness.py --mn1-w 3u --mp1-w 6u
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("m1")

ROOT = Path(__file__).parent


def parse_si(s: str) -> float:
    """Convert '2u' → 2e-6, '180n' → 180e-9, etc."""
    mul = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3}
    s = s.strip().lower()
    if s and s[-1] in mul:
        return float(s[:-1]) * mul[s[-1]]
    return float(s)


def main() -> None:
    parser = argparse.ArgumentParser(description="M1 Manual Plumbing Test")
    parser.add_argument("--mn1-w", default="2u",   help="NMOS width  (e.g. '2u')")
    parser.add_argument("--mp1-w", default="4u",   help="PMOS width  (e.g. '4u')")
    parser.add_argument("--mn1-l", default="180n", help="NMOS length (e.g. '180n')")
    parser.add_argument("--mp1-l", default="180n", help="PMOS length (e.g. '180n')")
    parser.add_argument("--spec",  default=str(ROOT / "spec.yaml"))
    args = parser.parse_args()

    # ------------------------------------------------------------------
    # 1. Load spec
    # ------------------------------------------------------------------
    from core.spec_checker import load_spec, verdict_str
    spec = load_spec(args.spec)
    logger.info("Spec loaded: %s", spec.circuit_name)
    logger.info("DC  template: %s", spec.template_dc)
    logger.info("TRAN template: %s", spec.template_tran)

    # ------------------------------------------------------------------
    # 2. Build candidate
    # ------------------------------------------------------------------
    from engines.base import Candidate
    candidate = Candidate(
        values={
            "mn1_w": parse_si(args.mn1_w),
            "mp1_w": parse_si(args.mp1_w),
            "mn1_l": parse_si(args.mn1_l),
            "mp1_l": parse_si(args.mp1_l),
        },
        run_id=0,
        reason="M1 manual harness test",
    )
    logger.info("Candidate values: %s", candidate.values)
    logger.info("SPICE params:     %s", candidate.to_spice_params())

    # ------------------------------------------------------------------
    # 3. Write netlists (DC + TRAN)
    # ------------------------------------------------------------------
    from core.netlist_editor import NetlistEditor
    editor = NetlistEditor(spec, sim_root=ROOT / "simulations")
    dc_path, tran_path = editor.write(candidate, iteration=0, candidate_idx=0)
    logger.info("DC  netlist: %s", dc_path)
    logger.info("TRAN netlist: %s", tran_path)

    # ------------------------------------------------------------------
    # 4. Run LTspice
    # ------------------------------------------------------------------
    from core.sim_executor import SimExecutor
    executor = SimExecutor(parallel_sims=1, timeout_s=120)
    logger.info("Running LTspice (DC + TRAN)...")
    sim_results = executor.run_batch([(dc_path, tran_path)])
    sim = sim_results[0]

    if sim.sim_failed:
        logger.error("Simulation FAILED.")
        logger.error("  DC failed:   %s  (raw: %s)", sim.dc_failed,   sim.dc_raw)
        logger.error("  TRAN failed: %s  (raw: %s)", sim.tran_failed, sim.tran_raw)
        sys.exit(1)

    logger.info("Simulation complete (%.1f s)", sim.elapsed_s)
    logger.info("DC  log: %s", sim.dc_log)
    logger.info("TRAN log: %s", sim.tran_log)

    # ------------------------------------------------------------------
    # 5. Parse metrics
    # ------------------------------------------------------------------
    from core.result_parser import extract_metrics
    metrics = extract_metrics(
        dc_log=sim.dc_log, tran_log=sim.tran_log,
        dc_raw=sim.dc_raw, tran_raw=sim.tran_raw,
    )
    logger.info("Raw metrics: %s", metrics)

    # ------------------------------------------------------------------
    # 6. Score
    # ------------------------------------------------------------------
    logger.info("")
    logger.info("=" * 55)
    logger.info("RESULT: %s", verdict_str(metrics, spec))
    logger.info("=" * 55)
    logger.info("  Vm   = %.4f V   (target %.4f V ± %.0f%%)",
                metrics.get("vm", float("nan")), spec.vm_target, spec.vm_tolerance * 100)
    logger.info("  tpd  = %.3f ns  (max %.3f ns)",
                metrics.get("tpd", float("nan")) * 1e9, spec.tpd_max * 1e9)
    logger.info("  tpHL = %.3f ns", metrics.get("tphl", float("nan")) * 1e9)
    logger.info("  tpLH = %.3f ns", metrics.get("tplh", float("nan")) * 1e9)


if __name__ == "__main__":
    main()
