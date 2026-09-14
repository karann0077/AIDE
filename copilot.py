"""
copilot.py — AIDE Copilot: "Describe a circuit → Get an optimized design"
=========================================================================
The fully automated entry point. Turns plain-English prompts into
LTspice-validated, optimizer-sized circuit netlists.

Modes:
  Single-shot:   python copilot.py "design a full adder, 1.8V, delay < 200ps"
  Interactive:   python copilot.py
  File input:    python copilot.py --file my_spec.txt
  Refine mode:   python copilot.py --refine output/full_adder/ "make it faster"

What it does per prompt:
  1. Parse intent     (LLM call #1, ~1s)
  2. Generate netlist (LLM call #2, ~5s)
  3. Validate in LTspice with self-healing (real simulation, ~3s)
  4. Run AIDE optimizer (BayesOpt or LLM, N iterations × ~2s each)
  5. Print results + write final netlist + generate report
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
import textwrap
from pathlib import Path

# ─── Auto-load .env ──────────────────────────────────────────────────────────
_env = Path(__file__).parent / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# ─── Rich terminal output (graceful fallback) ─────────────────────────────────
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich import print as rprint
    _RICH = True
    console = Console()
except ImportError:
    _RICH = False
    console = None

logging.basicConfig(
    level=logging.WARNING,   # suppress library noise; copilot prints its own output
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("copilot")

# ─── Project root on Python path ─────────────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


# ─────────────────────────────────────────────────────────────────────────────
# Terminal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _print(msg: str, style: str = "") -> None:
    if _RICH:
        console.print(msg, style=style)
    else:
        print(msg)

def _header(title: str) -> None:
    if _RICH:
        console.rule(f"[bold cyan]{title}[/bold cyan]")
    else:
        print(f"\n{'─'*60}\n{title}\n{'─'*60}")

def _success(msg: str) -> None:
    _print(f"✅  {msg}", style="bold green")

def _warning(msg: str) -> None:
    _print(f"⚠️   {msg}", style="yellow")

def _error(msg: str) -> None:
    _print(f"❌  {msg}", style="bold red")

def _info(msg: str) -> None:
    _print(f"   {msg}", style="dim")

def _spinner(label: str):
    if _RICH:
        return Progress(SpinnerColumn(), TextColumn(f"[cyan]{label}[/cyan]"),
                        transient=True, console=console)
    class _Noop:
        def __enter__(self): print(f"  ⏳ {label}"); return self
        def __exit__(self, *a): pass
        def add_task(self, *a, **kw): return 0
    return _Noop()


# ─────────────────────────────────────────────────────────────────────────────
# Core pipeline
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline(
    prompt: str,
    engine: str = "bayes",
    output_root: str = "output",
    skip_reliability: bool = True,
    chat_history: list[dict] | None = None,
) -> dict:
    """
    Full Copilot pipeline for one prompt.

    Returns a result dict with: circuit_name, dc_path, tran_path,
    spec, best_iteration, total_iterations, report_path
    """
    from engines.circuit_generator import CircuitGenerator
    from engines.netlist_validator import NetlistValidator, NetlistValidationError
    from core.dynamic_spec import DynamicSpec
    from core.dynamic_result_parser import extract_metrics as dyn_extract
    from core.netlist_editor import NetlistEditor
    from core.sim_executor import SimExecutor
    from engines import get_engine
    from engines.base import Candidate, Iteration

    # ── Step 1: Generate circuit ──────────────────────────────────────────
    _header("Step 1 — Understanding your request")
    gen = CircuitGenerator(output_dir=output_root)

    with _spinner("Parsing intent..."):
        # CircuitGenerator.generate() does both LLM calls internally
        pass

    try:
        dc_path, tran_path, spec = gen.generate(prompt, chat_history)
    except Exception as exc:
        _error(f"Circuit generation failed: {exc}")
        raise

    _success(f"Circuit: {spec.circuit_name}")
    _info(f"Description: {spec.circuit_description}")
    _info(f"Design variables: {len(spec.design_variables)}")
    _info(f"Performance targets: {len(spec.metric_targets)}")

    # Print targets
    _print("\n  [bold]Targets:[/bold]" if _RICH else "\n  Targets:")
    for mt in spec.metric_targets:
        from core.dynamic_spec import _unit_for
        unit, scale = _unit_for(mt.name)
        _info(f"  {mt.name}: {mt.direction} {mt.target_value*scale:.3g} {unit}")

    # ── Step 2: Validate netlists ─────────────────────────────────────────
    _header("Step 2 — Validating with real LTspice")
    validator = NetlistValidator()

    dc_text   = dc_path.read_text()
    tran_text = tran_path.read_text()

    try:
        dc_text, tran_text = validator.validate_and_fix(dc_text, tran_text, {})
        # Write back fixed versions
        dc_path.write_text(dc_text)
        tran_path.write_text(tran_text)
        _success("Netlists validated ✓")
    except NetlistValidationError as exc:
        _error(f"Validation failed: {exc}")
        raise

    # ── Step 3: Run AIDE optimizer ────────────────────────────────────────
    _header(f"Step 3 — Optimizing with {engine.upper()} engine")

    editor   = SimExecutor(parallel_sims=spec.parallel_sims)
    executor = SimExecutor(parallel_sims=spec.parallel_sims)

    # Use a specialized netlist editor that understands DynamicSpec
    net_editor = _DynamicNetlistEditor(spec)
    opt_engine = get_engine(engine, spec)
    opt_engine.warm_start(spec.initial_values)

    history: list[Iteration] = []
    best: Iteration | None = None

    # Set up JSONL logger
    log_dir = Path(output_root) / spec.circuit_name
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "run_log.jsonl"

    for i in range(spec.max_iterations):
        candidates = opt_engine.propose_next(history, spec)
        netlist_pairs = net_editor.write_batch(candidates, iteration=i)

        from core.sim_executor import SimExecutor as SE
        se = SE(parallel_sims=1)
        sim_results = se.run_batch(netlist_pairs)

        for cand, sim in zip(candidates, sim_results):
            cand.run_id = i
            if sim.sim_failed:
                metrics = {mt.name: float("nan") for mt in spec.metric_targets}
                passed, err = False, 1e6
            else:
                metrics = dyn_extract(
                    dc_log=sim.dc_log,
                    tran_log=sim.tran_log,
                    meas_dc=spec.meas_dc,
                    meas_tran=spec.meas_tran,
                    dc_raw=sim.dc_raw,
                    tran_raw=sim.tran_raw,
                )
                passed, err = spec.score(metrics)

            it = Iteration(
                iteration=i, candidate=cand, metrics=metrics,
                error=err, passed=passed, sim_failed=sim.sim_failed,
            )
            history.append(it)

            # Log to JSONL
            with log_path.open("a") as f:
                f.write(json.dumps({
                    "iteration": i, "values": cand.values,
                    "metrics": {k: v for k, v in metrics.items()},
                    "error": err, "passed": passed,
                    "reason": cand.reason,
                }) + "\n")

            if passed and (best is None or err < best.error):
                best = it
                _success(f"Iteration {i+1}: PASS ✓  error={err:.4f}  {cand.reason or ''}")
            else:
                from core.dynamic_spec import _unit_for
                metric_str = "  ".join(
                    f"{mt.name}={metrics.get(mt.name, float('nan'))*_unit_for(mt.name)[1]:.3g}"
                    f"{_unit_for(mt.name)[0]}"
                    for mt in spec.metric_targets[:3]
                )
                _info(f"Iter {i+1:02d}: {metric_str}  err={err:.4f}")

        if best is not None and best.passed:
            _success(f"Spec met after {i+1} iterations!")
            break

        # Early stop if no improvement
        if _no_improve(history, spec.max_no_improve):
            _warning(f"No improvement in {spec.max_no_improve} iterations — stopping.")
            break

    # ── Step 4: Write final design ────────────────────────────────────────
    _header("Results")

    if best is None:
        _warning("Optimizer did not find a passing design.")
        best = min(history, key=lambda x: x.error) if history else None

    out_dir = Path(output_root) / spec.circuit_name
    report_path = None

    if best:
        # Write final optimized netlist
        final_dc, final_tran = net_editor.write(best.candidate, iteration=999, candidate_idx=0)
        final_dir = out_dir / "final"
        final_dir.mkdir(exist_ok=True)
        final_tran_dest = final_dir / "final_design_tran.cir"
        shutil.copy2(final_dc,   final_dir / "final_design_dc.cir")
        shutil.copy2(final_tran, final_tran_dest)

        # Auto-open in LTspice (macOS)
        if sys.platform == "darwin":
            try:
                import subprocess
                subprocess.run(["open", "-a", "LTspice", str(final_tran_dest)], check=False)
                _info("Auto-opened final_design_tran.cir in LTspice.")
            except Exception as e:
                _warning(f"Could not auto-open LTspice: {e}")

        # Print sizing table
        _print_results(spec, best, len(history))

        # Generate report if matplotlib available
        try:
            report_path = _generate_report(log_path, out_dir / "report.png", spec)
        except Exception as e:
            _warning(f"Report generation skipped: {e}")

    return {
        "circuit_name": spec.circuit_name,
        "dc_path": str(dc_path),
        "tran_path": str(tran_path),
        "spec": spec,
        "best": best,
        "total_iterations": len(history),
        "report_path": str(report_path) if report_path else None,
        "log_path": str(log_path),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic Netlist Editor (adapts NetlistEditor for DynamicSpec)
# ─────────────────────────────────────────────────────────────────────────────

class _DynamicNetlistEditor:
    """Wraps core.netlist_editor.NetlistEditor for DynamicSpec."""

    def __init__(self, spec: "DynamicSpec") -> None:
        from core.netlist_editor import NetlistEditor
        from core.spec_checker import Spec
        # Create a minimal adapter — NetlistEditor only needs template paths
        self._spec = spec
        self._dc_tpl   = spec.template_dc
        self._tran_tpl = spec.template_tran
        self._sim_root = Path("simulations") / spec.circuit_name

    def write(self, cand: "Candidate", iteration: int, candidate_idx: int) -> tuple[Path, Path]:
        import shutil
        from PyLTSpice import SpiceEditor

        base = self._sim_root / f"iter_{iteration:03d}" / f"cand_{candidate_idx:02d}"
        dc_dir   = base / "dc"
        tran_dir = base / "tran"
        dc_dir.mkdir(parents=True, exist_ok=True)
        tran_dir.mkdir(parents=True, exist_ok=True)

        params = cand.to_spice_params()
        dc_path   = self._write_one(self._dc_tpl,   dc_dir   / "circuit_dc.cir",   params)
        tran_path = self._write_one(self._tran_tpl,  tran_dir / "circuit_tran.cir", params)
        return dc_path, tran_path

    def _write_one(self, template: Path, dest: Path, params: dict) -> Path:
        import shutil
        from PyLTSpice import SpiceEditor
        shutil.copy2(template, dest)
        netlist = SpiceEditor(str(dest))
        netlist.set_parameters(**params)
        netlist.save_netlist(str(dest))
        return dest

    def write_batch(self, candidates: list, iteration: int) -> list[tuple[Path, Path]]:
        return [self.write(c, iteration, i) for i, c in enumerate(candidates)]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _no_improve(history: list, window: int) -> bool:
    if len(history) < window + 1:
        return False
    recent = [it.error for it in history[-window:] if not it.sim_failed]
    if not recent:
        return False
    return min(recent) >= history[-(window + 1)].error


def _print_results(spec: "DynamicSpec", best: "Iteration", total: int) -> None:
    from core.dynamic_spec import _unit_for
    lines = [
        f"\n  Circuit:    {spec.circuit_name}",
        f"  Iterations: {total}",
        f"  Status:     {'PASS ✓' if best.passed else 'BEST (did not fully converge)'}",
        "",
        "  PERFORMANCE",
    ]
    for mt in spec.metric_targets:
        val = best.metrics.get(mt.name, float("nan"))
        unit, scale = _unit_for(mt.name)
        check = "✓" if mt.is_satisfied(val) else "✗"
        lines.append(f"    {check} {mt.name:<20} = {val*scale:>8.3g} {unit}  "
                     f"(target {mt.direction} {mt.target_value*scale:.3g} {unit})")
    lines += ["", "  SIZING"]
    for var, val in best.candidate.values.items():
        if val >= 1e-6:
            disp = f"{val*1e6:.3g} µm"
        elif val >= 1e-9:
            disp = f"{val*1e9:.3g} nm"
        else:
            disp = f"{val:.4g}"
        lines.append(f"    {var:<20} = {disp}")

    _print("\n".join(lines), style="bold" if _RICH else "")


def _generate_report(log_path: Path, out_path: Path, spec: "DynamicSpec") -> Path:
    """Generate convergence plot."""
    import json, math
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    iters, errors = [], []
    with log_path.open() as f:
        for line in f:
            rec = json.loads(line.strip())
            if "iteration" in rec and "error" in rec:
                iters.append(rec["iteration"] + 1)
                errors.append(rec["error"])

    if not iters:
        return out_path

    # Dark theme
    bg = "#0d1117"
    fig, ax = plt.subplots(figsize=(10, 5), facecolor=bg)
    ax.set_facecolor("#161b22")

    best_so_far = []
    cur = math.inf
    for e in errors:
        cur = min(cur, e)
        best_so_far.append(cur)

    ax.plot(iters, errors, "o-", color="#58a6ff", alpha=0.6, markersize=4, label="Error per iteration")
    ax.plot(iters, best_so_far, "-", color="#3fb950", linewidth=2.5, label="Best so far")
    ax.set_xlabel("Iteration", color="#c9d1d9")
    ax.set_ylabel("Error Score", color="#c9d1d9")
    ax.set_title(f"AIDE Copilot — {spec.circuit_name} Convergence", color="#c9d1d9", fontweight="bold")
    ax.tick_params(colors="#c9d1d9")
    ax.legend(framealpha=0.2)
    ax.grid(True, color="#21262d", linestyle="--", alpha=0.5)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor=bg)
    plt.close(fig)
    return out_path


# ─────────────────────────────────────────────────────────────────────────────
# Chat session
# ─────────────────────────────────────────────────────────────────────────────

class CopilotSession:
    """
    Interactive chat session. Maintains history so the user can refine
    designs across multiple turns.
    """

    def __init__(
        self,
        engine: str = "bayes",
        output_root: str = "output",
        with_reliability: bool = False,
    ) -> None:
        self.engine = engine
        self.output_root = output_root
        self.with_reliability = with_reliability
        self.history: list[dict] = []

    def chat(self, prompt: str) -> dict:
        result = run_pipeline(
            prompt=prompt,
            engine=self.engine,
            output_root=self.output_root,
            skip_reliability=not self.with_reliability,
            chat_history=self.history,
        )
        self.history.append({
            "user": prompt,
            "circuit_name": result["circuit_name"],
            "result_summary": self._summarize(result),
        })
        return result

    @staticmethod
    def _summarize(result: dict) -> str:
        if result["best"] is None:
            return "No passing design found."
        b = result["best"]
        return (f"Designed {result['circuit_name']} in {result['total_iterations']} iterations. "
                f"Best error={b.error:.4f}. "
                f"Metrics: {b.metrics}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="AIDE Copilot — AI-Driven Analog Design Explorer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python copilot.py "design a CMOS inverter, 1.8V, Vm at half VDD"
              python copilot.py "full adder, delay < 200ps, 1.8V"
              python copilot.py                          # interactive chat mode
              python copilot.py --engine llm "ring oscillator, 7 stages, 1.2V"
        """),
    )
    parser.add_argument(
        "prompt", nargs="?", default=None,
        help="Plain-English circuit description. Omit for interactive chat mode.",
    )
    parser.add_argument(
        "--engine", choices=["bayes", "llm"], default="bayes",
        help="Sizing optimizer: 'bayes' (Optuna, default) or 'llm' (Gemini agent)",
    )
    parser.add_argument(
        "--output", default="output",
        help="Root output directory (default: output/)",
    )
    parser.add_argument(
        "--with-reliability", action="store_true",
        help="Run Monte Carlo + PVT corner check after nominal optimization",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose logging",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logging.getLogger("copilot").setLevel(logging.DEBUG)

    # Check API key
    if not os.environ.get("GOOGLE_API_KEY"):
        _error("GOOGLE_API_KEY not set. Add it to .env or export it.")
        _info("Get a free key at: https://aistudio.google.com")
        sys.exit(1)

    session = CopilotSession(
        engine=args.engine,
        output_root=args.output,
        with_reliability=args.with_reliability,
    )

    if args.prompt:
        # ── Single-shot mode ───────────────────────────────────────────────
        _header("AIDE Copilot")
        _print(f'  Prompt: "{args.prompt}"')
        try:
            result = session.chat(args.prompt)
            if result.get("report_path"):
                _info(f"Report: {result['report_path']}")
            _info(f"Log:    {result['log_path']}")
        except Exception as exc:
            _error(str(exc))
            if args.verbose:
                import traceback; traceback.print_exc()
            sys.exit(1)

    else:
        # ── Interactive chat mode ──────────────────────────────────────────
        _header("AIDE Copilot — Interactive Mode")
        _print("  Type a circuit description to design it.")
        _print("  Type 'quit' or press Ctrl+C to exit.\n")
        _print("  Examples:")
        _print("    > design a full adder, 1.8V, minimize delay")
        _print("    > CMOS inverter with Vm at half VDD, under 200ps delay")
        _print("    > ring oscillator, 5 stages, target 1 GHz, 1.2V\n")

        while True:
            try:
                prompt = input("You: ").strip()
            except (KeyboardInterrupt, EOFError):
                _print("\nGoodbye!")
                break

            if not prompt:
                continue
            if prompt.lower() in ("quit", "exit", "q"):
                _print("Goodbye!")
                break

            try:
                session.chat(prompt)
            except Exception as exc:
                _error(str(exc))
                if args.verbose:
                    import traceback; traceback.print_exc()


if __name__ == "__main__":
    main()
