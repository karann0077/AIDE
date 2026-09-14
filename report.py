"""
report.py — Post-run Visualisation Script
==========================================
Reads run_log.jsonl and generates:
  1. Convergence plot  — error vs. iteration
  2. Sizing trajectory — how each design variable evolved
  3. Monte Carlo histogram — Vm and tpd distributions

Usage:
    python report.py                         # uses run_log.jsonl in CWD
    python report.py --log path/to/log.jsonl --out reports/
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless — save to files
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np

# ---------------------------------------------------------------------------
# Style
# ---------------------------------------------------------------------------
PALETTE = {
    "bg":       "#0d1117",
    "surface":  "#161b22",
    "accent1":  "#58a6ff",
    "accent2":  "#3fb950",
    "accent3":  "#f78166",
    "accent4":  "#d2a8ff",
    "text":     "#c9d1d9",
    "grid":     "#21262d",
}

plt.rcParams.update({
    "figure.facecolor":  PALETTE["bg"],
    "axes.facecolor":    PALETTE["surface"],
    "axes.edgecolor":    PALETTE["grid"],
    "axes.labelcolor":   PALETTE["text"],
    "axes.titlecolor":   PALETTE["text"],
    "xtick.color":       PALETTE["text"],
    "ytick.color":       PALETTE["text"],
    "grid.color":        PALETTE["grid"],
    "grid.linestyle":    "--",
    "grid.alpha":        0.5,
    "text.color":        PALETTE["text"],
    "font.family":       "DejaVu Sans",
    "font.size":         10,
    "lines.linewidth":   1.8,
    "figure.dpi":        150,
})


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_log(log_path: Path) -> tuple[list[dict], list[dict]]:
    """Parse run_log.jsonl into (iterations, reliability_records)."""
    iterations, reliability = [], []
    with log_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("type") == "reliability":
                reliability.append(rec)
            else:
                iterations.append(rec)
    return iterations, reliability


# ---------------------------------------------------------------------------
# Plot 1: Convergence curve
# ---------------------------------------------------------------------------
def plot_convergence(iters: list[dict], ax: plt.Axes) -> None:
    errors = [it["error"] for it in iters]
    passed = [it["passed"] for it in iters]
    x = list(range(1, len(errors) + 1))

    # Running minimum (best-so-far envelope)
    best_so_far = []
    cur_min = math.inf
    for e in errors:
        cur_min = min(cur_min, e)
        best_so_far.append(cur_min)

    ax.plot(x, errors, "o-", color=PALETTE["accent1"], alpha=0.7, label="Error per iteration", markersize=4)
    ax.plot(x, best_so_far, "-", color=PALETTE["accent2"], linewidth=2.5, label="Best-so-far")

    # Mark PASS iterations
    pass_x = [xi for xi, p in zip(x, passed) if p]
    pass_y = [errors[i - 1] for i in pass_x]
    if pass_x:
        ax.scatter(pass_x, pass_y, marker="★", s=150, color=PALETTE["accent2"], zorder=5, label="PASS")

    ax.set_xlabel("Iteration")
    ax.set_ylabel("Error Score (lower = better)")
    ax.set_title("Convergence Curve", fontweight="bold")
    ax.legend(framealpha=0.15)
    ax.grid(True)
    ax.set_xlim(0.5, len(x) + 0.5)


# ---------------------------------------------------------------------------
# Plot 2: Sizing trajectory
# ---------------------------------------------------------------------------
def plot_sizing(iters: list[dict], ax: plt.Axes) -> None:
    if not iters:
        return
    
    # Get dynamic list of variables from the first iteration
    vars_of_interest = list(iters[0]["values"].keys())[:4]  # Plot up to 4 vars
    
    colors = [PALETTE["accent1"], PALETTE["accent3"], PALETTE["accent4"], PALETTE["accent2"]]
    x = list(range(1, len(iters) + 1))

    for var, color in zip(vars_of_interest, colors + [PALETTE["text"]] * 10):
        ys = [it["values"].get(var, float("nan")) * 1e6 for it in iters]  # → µm
        ax.plot(x, ys, "o-", color=color, alpha=0.85, label=f"{var} (µm)", markersize=4)

    ax.set_xlabel("Iteration")
    ax.set_ylabel("Width (µm)")
    ax.set_title("Device Width Trajectory", fontweight="bold")
    ax.legend(framealpha=0.15)
    ax.grid(True)


# ---------------------------------------------------------------------------
# Plot 3: Vm trace
# ---------------------------------------------------------------------------
def plot_vm_trace(iters: list[dict], ax: plt.Axes, spec_vm_target: float | None) -> None:
    x = list(range(1, len(iters) + 1))
    vms = [it["metrics"].get("vm", float("nan")) for it in iters]
    ax.plot(x, vms, "o-", color=PALETTE["accent4"], alpha=0.9, markersize=4, label="Vm (V)")
    if spec_vm_target is not None:
        ax.axhline(spec_vm_target, color=PALETTE["accent2"], linestyle="--", linewidth=1.5, label=f"Target {spec_vm_target:.3g} V")
    ax.set_xlabel("Iteration")
    ax.set_ylabel("Vm (V)")
    ax.set_title("Switching Threshold (Vm) per Iteration", fontweight="bold")
    ax.legend(framealpha=0.15)
    ax.grid(True)


# ---------------------------------------------------------------------------
# Plot 4: tpd trace
# ---------------------------------------------------------------------------
def plot_tpd_trace(iters: list[dict], ax: plt.Axes, tpd_max_ns: float | None) -> None:
    x = list(range(1, len(iters) + 1))
    tpds = [it["metrics"].get("tpd", float("nan")) * 1e9 for it in iters]
    ax.plot(x, tpds, "o-", color=PALETTE["accent3"], alpha=0.9, markersize=4, label="tpd (ns)")
    if tpd_max_ns is not None:
        ax.axhline(tpd_max_ns, color=PALETTE["accent2"], linestyle="--", linewidth=1.5, label=f"Max {tpd_max_ns:.3g} ns")
    ax.set_xlabel("Iteration")
    ax.set_ylabel("tpd (ns)")
    ax.set_title("Propagation Delay per Iteration", fontweight="bold")
    ax.legend(framealpha=0.15)
    ax.grid(True)


# ---------------------------------------------------------------------------
# Plot 5: Monte Carlo histogram (if reliability data present)
# ---------------------------------------------------------------------------
def plot_mc_histogram(reliability: list[dict], ax: plt.Axes) -> None:
    if not reliability:
        ax.text(0.5, 0.5, "No Monte Carlo data", transform=ax.transAxes,
                ha="center", va="center", color=PALETTE["text"])
        ax.set_title("Monte Carlo — Vm Distribution")
        return

    rel = reliability[-1]
    mean = rel.get("mc_vm_mean", float("nan"))
    std = rel.get("mc_vm_std", float("nan"))
    samples = np.asarray(rel.get("mc_vm_samples", []), dtype=float)

    if math.isnan(mean) or math.isnan(std) or std == 0 or samples.size == 0:
        ax.text(0.5, 0.5, "MC data unavailable", transform=ax.transAxes,
                ha="center", va="center", color=PALETTE["text"])
        return

    ax.hist(samples, bins=max(10, min(50, len(samples)//4)), color=PALETTE["accent1"], alpha=0.75, edgecolor="none", label=f"MC samples (N={len(samples)})")

    # ±3σ lines
    for sigma, style in [(-3, ":"), (-2, "--"), (2, "--"), (3, ":")]:
        ax.axvline(mean + sigma * std, color=PALETTE["accent3"], linestyle=style, linewidth=1.2,
                   label=f"{sigma}σ" if abs(sigma) == 3 else None)

    ax.axvline(mean, color=PALETTE["accent2"], linewidth=2, label=f"Mean {mean:.4f} V")
    ax.set_xlabel("Vm (V)")
    ax.set_ylabel("Count")
    ax.set_title(f"Monte Carlo — Vm Distribution  (σ={std*1000:.2f} mV)", fontweight="bold")
    ax.legend(framealpha=0.15)
    ax.grid(True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Generate run report")
    parser.add_argument("--log", default="run_log.jsonl", help="Path to run_log.jsonl")
    parser.add_argument("--out", default="reports", help="Output directory for plots")
    parser.add_argument("--vm-target", type=float, default=0.9, help="Vm target in volts")
    parser.add_argument("--tpd-max", type=float, default=0.2, help="tpd max in ns")
    args = parser.parse_args()

    log_path = Path(args.log)
    if not log_path.exists():
        print(f"Log file not found: {log_path}")
        return

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    iters, reliability = load_log(log_path)

    if not iters:
        print("No iteration data found in log.")
        return

    print(f"Loaded {len(iters)} iterations, {len(reliability)} reliability records.")

    # -----------------------------------------------------------------------
    # Main 2×3 figure
    # -----------------------------------------------------------------------
    fig = plt.figure(figsize=(18, 12))
    fig.patch.set_facecolor(PALETTE["bg"])
    gs = gridspec.GridSpec(2, 3, figure=fig, hspace=0.42, wspace=0.35)

    plot_convergence(iters,    fig.add_subplot(gs[0, 0]))
    plot_sizing(iters,         fig.add_subplot(gs[0, 1]))
    plot_vm_trace(iters,       fig.add_subplot(gs[0, 2]), args.vm_target)
    plot_tpd_trace(iters,      fig.add_subplot(gs[1, 0]), args.tpd_max)
    plot_mc_histogram(reliability, fig.add_subplot(gs[1, 1:]))

    # -----------------------------------------------------------------------
    # Title + metadata
    # -----------------------------------------------------------------------
    fig.suptitle(
        "Agentic LTspice Sizing Loop — Run Report",
        fontsize=16, fontweight="bold", color=PALETTE["text"], y=1.01,
    )

    # Sizing table (text box)
    best_iters = [it for it in iters if it["passed"]]
    if best_iters:
        best = min(best_iters, key=lambda x: x["error"])
        table_lines = ["Best design:"]
        for var, val in best["values"].items():
            table_lines.append(f"  {var}: {val*1e6:.2f} µm")
        table_lines.append(f"  Vm:  {best['metrics'].get('vm', float('nan')):.4f} V")
        table_lines.append(f"  tpd: {best['metrics'].get('tpd', float('nan'))*1e9:.3f} ns")
        table_lines.append(f"  err: {best['error']:.4f}")
        fig.text(
            0.37, 0.01, "\n".join(table_lines),
            color=PALETTE["accent2"], fontsize=9,
            va="bottom", ha="center",
            bbox=dict(boxstyle="round", facecolor=PALETTE["surface"], alpha=0.8, edgecolor=PALETTE["grid"]),
        )

    out_path = out_dir / "report.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor=PALETTE["bg"])
    print(f"Report saved to: {out_path}")
    plt.close(fig)


if __name__ == "__main__":
    main()
