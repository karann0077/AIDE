"""
orchestrator.py — Main loop controller
=======================================
Ties together all components into the full agentic sizing loop.

Usage (CLI):
    python orchestrator.py --spec spec.yaml --engine bayes
    python orchestrator.py --spec spec.yaml --engine llm
    python orchestrator.py --spec spec.yaml --engine bayes --skip-reliability

The orchestrator runs in two phases:
  Phase 1 (inner loop):  converge nominal design to meet spec at 27°C / VDD_nom
  Phase 2 (outer loop):  run reliability/corner check; tighten and re-enter if needed
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

# Auto-load .env if present (so GOOGLE_API_KEY etc. are available without shell export)
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from core.spec_checker import Spec, load_spec, score, verdict_str
from core.netlist_editor import NetlistEditor
from core.sim_executor import SimExecutor
from core.result_parser import extract_metrics
from engines import get_engine
from engines.base import Candidate, Iteration
from reliability.corner_stage import CornerStage

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("orchestrator")


# ---------------------------------------------------------------------------
# JSONL logger
# ---------------------------------------------------------------------------
class IterationLogger:
    """Appends one JSON line per iteration to run_log.jsonl."""

    def __init__(self, log_path: Path) -> None:
        self.path = log_path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, it: Iteration) -> None:
        record = {
            "iteration": it.iteration,
            "run_id": it.candidate.run_id,
            "values": it.candidate.values,
            "reason": it.candidate.reason,
            "metrics": it.metrics,
            "error": it.error,
            "passed": it.passed,
            "sim_failed": it.sim_failed,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def log_reliability(self, result: dict) -> None:
        record = {"type": "reliability", **result, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# Convergence check
# ---------------------------------------------------------------------------
def no_improvement(history: list[Iteration], window: int) -> bool:
    """Return True if the error has not improved in the last `window` iters."""
    if len(history) < window:
        return False
    recent = [it.error for it in history[-window:] if not it.sim_failed]
    if not recent:
        return False
    return min(recent) >= history[-window - 1].error if len(history) > window else False


# ---------------------------------------------------------------------------
# Core inner loop
# ---------------------------------------------------------------------------
def run_inner_loop(
    spec: Spec,
    engine_name: str,
    editor: NetlistEditor,
    executor: SimExecutor,
    it_logger: IterationLogger,
    history: list[Iteration],
    iteration_offset: int = 0,
) -> tuple[list[Iteration], Iteration | None]:
    """
    Run the nominal sizing loop.

    Returns:
      (history, best_iteration_or_None)
    """
    engine = get_engine(engine_name, spec)
    engine.warm_start(spec.initial_values)

    best: Iteration | None = None
    max_iter = spec.max_iterations

    logger.info("=" * 60)
    logger.info("INNER LOOP  engine=%s  budget=%d iters", engine_name, max_iter)
    logger.info("=" * 60)

    for i in range(max_iter):
        iter_num = i + iteration_offset

        # 1. Ask engine for next candidates
        candidates = engine.propose_next(history, spec)
        logger.info("─── Iteration %d/%d ───", iter_num + 1, max_iter + iteration_offset)

        # 2. Write netlists (returns list of (dc_path, tran_path))
        netlist_pairs = editor.write_batch(candidates, iteration=iter_num)

        # 3. Simulate
        sim_results = executor.run_batch(netlist_pairs)

        # 4. Parse & score each candidate
        for cand, sim in zip(candidates, sim_results):
            cand.run_id = iter_num

            if sim.sim_failed:
                metrics: dict = {"vm": float("nan"), "tpd": float("nan")}
                passed = False
                err = 1e6
                logger.warning("  Sim FAILED for candidate %s", cand.values)
            else:
                metrics = extract_metrics(
                    dc_log=sim.dc_log, tran_log=sim.tran_log,
                    dc_raw=sim.dc_raw,  tran_raw=sim.tran_raw,
                )
                passed, err = score(metrics, spec)
                logger.info("  %s", verdict_str(metrics, spec))
                if cand.reason:
                    logger.info("  Reason: %s", cand.reason)

            it = Iteration(
                iteration=iter_num,
                candidate=cand,
                metrics=metrics,
                error=err,
                passed=passed,
                sim_failed=sim.sim_failed,
            )
            history.append(it)
            it_logger.log(it)

            if passed and (best is None or err < best.error):
                best = it
                logger.info("  ★ New best! error=%.4f", err)

        # 5. Stop conditions
        if best is not None and best.passed:
            logger.info("Nominal spec MET at iteration %d!", iter_num + 1)
            break

        if no_improvement(history, spec.max_no_improve):
            logger.warning(
                "No improvement in %d iterations — stopping early.", spec.max_no_improve
            )
            break

    return history, best


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Agentic LTspice Design-Sizing Loop"
    )
    parser.add_argument(
        "--spec", default="spec.yaml", help="Path to spec.yaml"
    )
    parser.add_argument(
        "--engine",
        choices=["bayes", "llm"],
        default="bayes",
        help="Decision engine: 'bayes' (Optuna) or 'llm' (LLM API)",
    )
    parser.add_argument(
        "--skip-reliability",
        action="store_true",
        help="Skip the Monte Carlo / corner reliability stage",
    )
    parser.add_argument(
        "--sim-root",
        default="simulations",
        help="Root directory for simulation working folders",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Path for JSONL run log (default: from spec.yaml)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable DEBUG logging"
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Load spec
    spec = load_spec(args.spec)
    logger.info("Loaded spec: %s  (DC: %s | TRAN: %s)", spec.circuit_name, spec.template_dc, spec.template_tran)

    # Set up components
    editor = NetlistEditor(spec, sim_root=args.sim_root)
    executor = SimExecutor(parallel_sims=spec.parallel_sims)
    log_path = Path(args.log_file or spec.reporting.get("log_file", "run_log.jsonl"))
    it_logger = IterationLogger(log_path)

    history: list[Iteration] = []
    best: Iteration | None = None
    reliability_retries = 0
    max_reliability_retries = 3

    # -----------------------------------------------------------------------
    # Outer reliability loop
    # -----------------------------------------------------------------------
    while True:
        # Phase 1: Inner sizing loop
        history, best = run_inner_loop(
            spec=spec,
            engine_name=args.engine,
            editor=editor,
            executor=executor,
            it_logger=it_logger,
            history=history,
            iteration_offset=len(history),
        )

        if best is None:
            logger.error("Inner loop FAILED to find any passing design. Exiting.")
            sys.exit(1)

        logger.info(
            "Inner loop complete. Best sizing: %s  error=%.4f",
            best.candidate.values, best.error,
        )

        # Phase 2: Reliability check
        if args.skip_reliability:
            logger.info("--skip-reliability: skipping corner/MC stage.")
            break

        # Regenerate the best netlist for the reliability stage (uses DC template)
        best_dc_path, _ = editor.write(
            best.candidate,
            iteration=best.iteration,
            candidate_idx=0,
        )

        corner_stage = CornerStage(spec, sim_root=Path(args.sim_root) / "reliability")
        corner_result = corner_stage.run(best_dc_path)

        it_logger.log_reliability({
            "mc_vm_mean": corner_result.mc_vm_mean,
            "mc_vm_std": corner_result.mc_vm_std,
            "mc_tpd_mean": corner_result.mc_tpd_mean,
            "mc_tpd_std": corner_result.mc_tpd_std,
            "corner_worst_vm": corner_result.corner_worst_vm,
            "corner_worst_tpd": corner_result.corner_worst_tpd,
            "reliability_passed": corner_result.passed,
        })

        if corner_result.passed:
            logger.info("★★★ RELIABILITY STAGE PASSED ★★★")
            break

        reliability_retries += 1
        if reliability_retries > max_reliability_retries:
            logger.error("Reliability failed after %d retries. Exiting.", reliability_retries)
            break

        # Tighten targets and re-enter inner loop
        logger.warning(
            "Reliability FAILED (retry %d/%d). Tightening targets and re-entering inner loop.",
            reliability_retries, max_reliability_retries,
        )
        if corner_result.suggested_vm_ratio is not None:
            spec.target["vm_ratio"] = corner_result.suggested_vm_ratio
            logger.info("  Tightened vm_ratio → %.4f", spec.target["vm_ratio"])
        if corner_result.suggested_tpd_max_ns is not None:
            spec.target["tpd_max_ns"] = corner_result.suggested_tpd_max_ns
            logger.info("  Tightened tpd_max_ns → %.4f", spec.target["tpd_max_ns"])

    # -----------------------------------------------------------------------
    # Final summary
    # -----------------------------------------------------------------------
    logger.info("\n" + "=" * 60)
    logger.info("FINAL DESIGN SIZING")
    logger.info("=" * 60)
    if best:
        for var, val in best.candidate.values.items():
            logger.info("  %-12s = %.4g", var, val)
        logger.info("  Final metrics: %s", best.metrics)
        logger.info("  Total iterations: %d", len(history))
    logger.info("Run log written to: %s", log_path)
    logger.info("Run 'python report.py' to generate plots.")


if __name__ == "__main__":
    main()
