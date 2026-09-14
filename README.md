<div align="center">

# 🔬 AIDE — AI-Driven Analog Design Explorer

**An autonomous analog IC sizing loop that closes the gap between a plain-English specification and a silicon-ready, reliability-verified design — using real LTspice as the simulation oracle.**

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![LTspice](https://img.shields.io/badge/Simulator-LTspice-8B0000?style=flat-square)](https://www.analog.com/en/design-center/design-tools-and-calculators/ltspice-simulator.html)
[![PyLTSpice](https://img.shields.io/badge/Automation-PyLTSpice-orange?style=flat-square)](https://pyltspice.readthedocs.io)
[![Optuna](https://img.shields.io/badge/Optimizer-Optuna-blueviolet?style=flat-square)](https://optuna.org)
[![Gemini](https://img.shields.io/badge/LLM-Gemini%20%7C%20GPT%20%7C%20Claude-4285F4?style=flat-square)](https://aistudio.google.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)

</div>

---

## What is AIDE?

AIDE (AI-Driven Analog Design Explorer) is an **agentic design-sizing loop** for analog circuits. You give it a performance spec in plain English — *"Vm at half VDD, propagation delay under 200 ps at 1.8 V, robust from −40°C to 125°C"* — and it autonomously:

1. **Proposes** the next set of component sizes (transistor W/L ratios)
2. **Edits** the LTspice netlist programmatically (no GUI, no hand-redraw)
3. **Runs** real LTspice simulations in batch mode
4. **Reads** the `.meas` results directly into Python
5. **Scores** the result against the spec
6. **Loops** until the design passes — then verifies it across Monte Carlo and PVT corners

No human in the loop between iterations. No manual cursor-reading. No spreadsheets.

---

## Demo: 4 Iterations to Convergence

```
Iteration 1 │ vm=0.651 V  tpd=114 ps  FAIL  (Vm too low)
Iteration 2 │ vm=0.598 V  tpd=766 ps  FAIL  (both off)
Iteration 3 │ vm=0.729 V  tpd= 31 ps  FAIL  (Vm still low)
Iteration 4 │ vm=0.945 V  tpd= 57 ps  PASS ★ (within spec)
```

> Running example: **CMOS inverter** (generalizes to any parametrized SPICE circuit)

---

## How It Works

```
┌─────────────────────────────────────────────────────┐
│                 spec.yaml (plain English + YAML)     │
│  "Vm = 0.5×VDD, tpd < 200 ps, −40°C to 125°C"      │
└───────────────────────┬─────────────────────────────┘
                        │
              ┌─────────▼──────────┐
              │   Decision Engine   │  ← BayesOpt (Optuna) or LLM (Gemini/GPT/Claude)
              │   propose_next()    │    Both implement the same Protocol interface
              └─────────┬──────────┘
                        │  proposed W/L values
              ┌─────────▼──────────┐
              │   Netlist Editor    │  ← PyLTSpice SpiceEditor
              │   edit .param       │    Writes isolated copy per candidate
              └─────────┬──────────┘
                        │  .cir files
              ┌─────────▼──────────┐
              │   Sim Executor      │  ← PyLTSpice SimRunner → real LTspice -b
              │   (DC + TRAN)       │    ~40 ms per simulation on Mac
              └─────────┬──────────┘
                        │  .raw + .log files
              ┌─────────▼──────────┐
              │   Result Parser     │  ← Reads .meas values from UTF-16 logs
              │   vm, tpd, tpHL...  │    Computes tpd = (|tpHL| + |tpLH|) / 2
              └─────────┬──────────┘
                        │  metrics dict
              ┌─────────▼──────────┐
              │   Spec Checker      │  ← Pure Python, zero simulator dependency
              │   pass/fail + error │    score() is fully unit-testable offline
              └─────────┬──────────┘
                        │ not met → back to Decision Engine
                        │ passed ↓
              ┌─────────▼──────────┐
              │  Reliability Stage  │  ← Monte Carlo (200 runs) + PVT corners
              │  corner_stage.py    │    3σ margin check; tighten & re-enter if tight
              └─────────┬──────────┘
                        │
              ┌─────────▼──────────┐
              │   report.py         │  ← Convergence plot + MC histogram + sizing table
              └────────────────────┘
```

### Two Brain Modes

| Engine | How it works | Why use it |
|---|---|---|
| **BayesOpt** (`--engine bayes`) | Optuna TPE sampler explores W/L space | Fast, deterministic, no API key needed |
| **LLM Agent** (`--engine llm`) | Sends spec + history to Gemini/GPT/Claude; asks for JSON proposal + one-line reason | Interprets *why* changes are needed; can reason about convergence failures |

The LLM engine is genuinely **agentic**: it states its reasoning (`"raise NMOS W/L, Vm is too high"`), and can be extended to read LTspice's own error logs and propose fixes — reasoning about the *tool's failures*, not just numeric outputs.

---

## Project Structure

```
agentic-ltspice/
│
├── spec.yaml                     # 📋 Single source of truth for design target
├── orchestrator.py               # 🔄 Main loop controller (inner + outer phases)
├── m1_manual_harness.py          # 🔧 M1 plumbing test — run this first
├── report.py                     # 📊 Post-run visualization (6-panel dark-mode figure)
├── requirements.txt
├── .env.example                  # API key template
│
├── templates/
│   ├── inverter_dc.cir           # DC netlist  → measures Vm
│   └── inverter_tran.cir         # TRAN netlist → measures tpHL, tpLH
│
├── engines/
│   ├── base.py                   # DecisionEngine Protocol + Candidate/Iteration types
│   ├── bayes_opt.py              # Optuna TPE engine (M2 baseline)
│   └── llm_agent.py             # LLM agent — Gemini / GPT-4o / Claude (M3)
│
├── core/
│   ├── spec_checker.py           # load_spec() + score() — pure Python, testable offline
│   ├── netlist_editor.py         # SpiceEditor wrapper — writes isolated per-candidate dirs
│   ├── sim_executor.py           # SimRunner wrapper — timeout + failure detection
│   └── result_parser.py          # UTF-16 log parser → metrics dict
│
├── reliability/
│   └── corner_stage.py           # Monte Carlo (200 runs) + PVT corners + σ-margin check
│
└── tests/
    ├── test_spec_checker.py      # 17 offline unit tests (no LTspice needed)
    └── test_candidate.py
```

---

## Prerequisites

| Requirement | Install |
|---|---|
| **LTspice XVII / 24** | [analog.com/ltspice](https://www.analog.com/en/design-center/design-tools-and-calculators/ltspice-simulator.html) — free |
| **Python ≥ 3.11** | [python.org](https://python.org) |
| **PyLTSpice** | `pip install PyLTSpice` |

> ⚠️ **Mac note:** AIDE has been tested on LTspice 17.2.4 for macOS. On Windows, the same workflow applies — PyLTSpice auto-detects the LTspice executable on both platforms.

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/karann0077/AIDE.git
cd AIDE/agentic-ltspice
pip install -r requirements.txt
```

### 2. (Optional) Set your LLM API key

```bash
cp .env.example .env
# Edit .env and paste your key — only needed for --engine llm
# GOOGLE_API_KEY=your_key_here
```

Get a free Gemini key at [aistudio.google.com](https://aistudio.google.com) → **Get API Key**.

### 3. Run the plumbing test (M1) — verifies LTspice is working

```bash
python m1_manual_harness.py
```

Expected output:
```
RESULT: FAIL ✗  vm=0.8505 V (target 0.9 V)  tpd=0.007 ns (max 0.200 ns)
```
*(FAIL is expected at the initial sizing — the optimizer will fix it)*

### 4. Run the optimization loop

**With Bayesian optimizer (no API key needed):**
```bash
python orchestrator.py --engine bayes --skip-reliability
```

**With LLM agent (Gemini / GPT / Claude):**
```bash
python orchestrator.py --engine llm --skip-reliability
```

**Full loop including Monte Carlo + PVT corners:**
```bash
python orchestrator.py --engine bayes
```

### 5. Generate the report

```bash
python report.py --vm-target 0.9 --tpd-max 0.2 --out reports/
# Opens reports/report.png — convergence plot + MC histogram + sizing table
```

---

## Configuration (`spec.yaml`)

Edit `spec.yaml` to change the design target — everything else adapts automatically.

```yaml
target:
  vm_ratio: 0.5        # Vm / VDD  (0.5 = symmetric switching)
  vm_tolerance: 0.05   # ±5% from ideal
  tpd_max_ns: 0.20     # max propagation delay in nanoseconds
  vdd_nominal: 1.8     # supply voltage

design_variables:
  mn1_w:               # NMOS width
    min:  0.2e-6
    max: 20.0e-6
    init: 2.0e-6

budget:
  max_iterations: 40
  max_no_improve: 8

llm:
  model: "gemini-2.5-flash"
  api_provider: "google"   # "google" | "openai" | "anthropic"
```

---

## Build Milestones

| # | Milestone | What it proves |
|---|---|---|
| **M1** | Manual harness — edit → simulate → parse | Plumbing works end-to-end |
| **M2** | Closed loop with Optuna BayesOpt | Automation: converges without any AI |
| **M3** | LLM agent engine (same interface, drop-in swap) | Agentic: reasons about *why* to change values |
| **M4** | Reliability outer loop (MC + PVT) | Production-readiness: passes corners, not just nominal |
| **M5** | JSONL logging + 6-panel report | Reproducibility: full audit trail of every decision |

All five milestones are **complete and tested**.

---

## Edge Cases Handled

| Case | Mitigation |
|---|---|
| LTspice crashes / op-point diverges | Detected via missing `.raw`; scored as worst-case error → optimizer avoids that region |
| LLM proposes unphysical value | Hard-clamped to `design_variables` bounds *before* it reaches the netlist |
| Runaway loop | Hard iteration budget + no-improvement window in `spec.yaml` |
| Parallel file collisions | Each candidate gets its own subfolder (`simulations/iter_N/cand_M/`) |
| LTspice Mac `.meas PARAM` limitation | `tpd` computed in Python from `tpHL` + `tpLH` |
| UTF-16-LE log encoding (Mac) | Result parser auto-detects and decodes correctly |

---

## What Makes This Agentic (Not Just Scripted)

The classical optimizer (M2) follows a fixed numerical rule chasing a scalar — it is **automation**.

The LLM engine (M3) is **agentic** in a meaningful sense:

- It states *why* it changed a value (stored in `run_log.jsonl` as the `reason` field)
- It adapts its strategy based on the *narrative* of the history, not just a loss surface
- It can be extended to read LTspice's own convergence-failure log and propose a fix ("add 1 Ω series resistance, op-point not converging") — **reasoning about the tool's failures**, not just its outputs

> *"Isn't this just hyperparameter tuning with extra steps?"*  
> No — a hyperparameter tuner doesn't explain itself, doesn't generalize across circuit topologies from context alone, and doesn't read an error log and propose a structural fix.

---

## Running the Unit Tests

```bash
python -m pytest tests/ -v
# 17 tests, all passing, zero LTspice dependency
```

---

## Tech Stack

| Layer | Tool |
|---|---|
| Simulator | LTspice (real engine, batch `-b` mode) |
| Automation | PyLTSpice — SpiceEditor, SimRunner, RawRead |
| Classical optimizer | Optuna (TPE Bayesian sampler) |
| Agentic brain | Google Gemini / OpenAI GPT-4o / Anthropic Claude |
| Orchestration | Pure Python |
| Logging | JSONL append-only run log |
| Visualization | matplotlib (dark-mode 6-panel figure) |

---

## License

MIT — see [LICENSE](LICENSE)

---

<div align="center">

Built as a proof-of-concept for agentic analog design automation.  
**The optimizer finds the numbers. The agent explains the reasoning. LTspice does the physics.**

</div>
