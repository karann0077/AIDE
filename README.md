<div align="center">

# 🔬 AIDE — AI-Driven Analog Design Explorer

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![LTspice](https://img.shields.io/badge/Simulator-LTspice-red.svg)](https://www.analog.com/ltspice)
[![Optuna](https://img.shields.io/badge/Optimization-Optuna-blueviolet.svg)](https://optuna.org/)

**An AI-driven analog design copilot that converts natural-language design requests into simulation-verified, optimized transistor sizing.**

AIDE combines **LLM-based circuit/specification generation, LTspice validation, Bayesian optimization, Monte Carlo analysis, and PVT testing** into an automated closed-loop design flow.

</div>

---

## 📌 What AIDE Does

Analog transistor sizing is usually iterative: choose device sizes, run LTspice, inspect measurements, adjust parameters, and repeat until the design meets its targets.

AIDE turns that process into an automated design-space exploration loop:

```text
Natural-language design request
              ↓
       LLM intent parsing
              ↓
     Dynamic design specification
              ↓
      LLM SPICE generation
              ↓
      LTspice validation
              ↓
      Bayesian / LLM sizing
              ↓
       Repeated simulation
              ↓
        Best candidate
              ↓
        PVT + Monte Carlo
              ↓
         Final report
```


For example:

> **Design a CMOS inverter with VDD = 1.8 V, switching point near 0.5×VDD, and propagation delay below 200 ps.**

AIDE extracts the circuit intent, design variables, measurable targets, and simulation requirements from the request and builds the optimization problem dynamically.

---

## 📸 See It In Action

The screenshots below follow the **actual execution order of AIDE**: the user first provides the design prompt in the terminal, AIDE interprets and optimizes the request, LTspice performs the circuit simulation, and the final optimized results are produced.

### 1. 📝 Define the Design Prompt — Terminal

![AIDE terminal prompt](screenshots/terminal1.png)

The workflow starts with a natural-language analog design request entered directly into the terminal.

### 2. 🤖 Agentic Design & Optimization — Terminal

![AIDE optimization process](screenshots/terminal2.png)

AIDE parses the request, creates a dynamic design specification, generates the SPICE netlists, validates them, and iteratively searches for transistor sizes that satisfy the requested objectives.

### 3. ⚡ Circuit Simulation — LTspice

![LTspice simulation](screenshots/ltspice.png)

LTspice is the physics and measurement backend. AIDE invokes the simulator for candidate designs and uses the measured circuit behavior to drive optimization.

### 4. 📊 Final Optimized Output

![AIDE final output](screenshots/output.png)

The workflow concludes with the optimized transistor sizing and measured performance used to determine whether the requested design targets were met.

---

## ✨ Key Capabilities

### 🤖 1. Natural-language → structured design specification

AIDE's first LLM stage acts as an intent parser. It converts a plain-English request into structured information such as:

- circuit/topology description
- supply voltage
- tunable device parameters and search bounds
- performance targets
- required `.meas` quantities
- PVT/corner requirements

This is represented internally by `DynamicSpec` rather than by a fixed inverter-only specification.

### 🧠 2. LLM-driven SPICE generation

A second LLM stage uses the structured intent plus the SPICE building-block library in `prompts/spice_blocks.md` to generate separate DC and transient LTspice netlists.

### 🩹 3. Self-healing LTspice validation

Generated netlists are run through LTspice before optimization. If the simulation fails or required measurements are missing, AIDE sends the error context back to the LLM and retries the correction, rather than passing an invalid netlist into the optimizer.

### 📐 4. Bayesian optimization

The default optimizer uses **Optuna/TPE Bayesian optimization** to explore the design-variable search space. Each candidate is written into the generated netlist, simulated, measured, and scored before the next candidate is proposed.

```text
Candidate parameters
        ↓
    Edit .param
        ↓
     LTspice
        ↓
  Extract metrics
        ↓
     Score spec
        ↓
  Next candidate
```

### 🧠 5. LLM-based optimization mode

AIDE also provides an alternative LLM decision engine. It receives the dynamic specification, variable semantics, previous measurements, and optimization history, then proposes the next sizing candidate as structured JSON.

The LLM output is clamped to the allowed design-variable bounds before it reaches the simulator.

### 🧪 6. PVT + Monte Carlo robustness testing

After nominal optimization, the reliability stage can evaluate the winning design across configurable temperature/supply corners and statistical device variation to estimate robustness and yield.

### 📊 7. Automated analysis and reporting

AIDE records optimization history in JSONL and can generate convergence, sizing, timing, and Monte Carlo visualizations from the simulation results.

---

## 🧠 Core Architecture

```text
                         Natural-language Prompt
                                  ↓
                         ┌─────────────────┐
                         │ LLM Intent      │
                         │ Parser          │
                         └────────┬────────┘
                                  ↓
                         ┌─────────────────┐
                         │ DynamicSpec     │
                         │ circuit         │
                         │ variables       │
                         │ targets         │
                         │ measurements    │
                         │ corners         │
                         └────────┬────────┘
                                  ↓
                         ┌─────────────────┐
                         │ LLM Netlist     │
                         │ Builder         │
                         └────────┬────────┘
                                  ↓
                         ┌─────────────────┐
                         │ Semantic +      │
                         │ LTspice        │
                         │ Validation      │
                         └────────┬────────┘
                                  ↓
                    ┌─────────────┴─────────────┐
                    ↓                           ↓
             ┌──────────────┐           ┌──────────────┐
             │   Optuna /   │           │  LLM Engine  │
             │ Bayesian Opt │           │  Alternative │
             └──────┬───────┘           └──────┬───────┘
                    └─────────────┬────────────┘
                                  ↓
                         ┌─────────────────┐
                         │ Candidate       │
                         │ transistor      │
                         │ sizing          │
                         └────────┬────────┘
                                  ↓
                         ┌─────────────────┐
                         │     LTspice     │
                         │   DC + TRAN     │
                         └────────┬────────┘
                                  ↓
                         ┌─────────────────┐
                         │ Dynamic Result  │
                         │ Parser          │
                         └────────┬────────┘
                                  ↓
                         ┌─────────────────┐
                         │ DynamicSpec     │
                         │ scoring         │
                         └────────┬────────┘
                                  ↓
                             PASS / FAIL
                                  ↓
                           Next iteration
                                  ↓
                            Best design
                                  ↓
                           PVT + Monte Carlo
                                  ↓
                            Final report
```

---

## 🔧 How the Prompt Becomes an Optimization Problem

AIDE does not require the user to manually write a circuit-specific YAML specification for the normal Copilot workflow.

For example, given:

```text
Design a CMOS inverter at 1.8 V with
Vm close to 0.9 V and delay below 200 ps.
```

the intent parser can produce a structured representation conceptually like:

```json
{
  "circuit_name": "cmos_inverter",
  "vdd": 1.8,
  "design_variables": [
    {"name": "mn_w", "min": 2e-7, "max": 2e-5},
    {"name": "mn_l", "min": 1.8e-7, "max": 2e-6},
    {"name": "mp_w", "min": 2e-7, "max": 4e-5},
    {"name": "mp_l", "min": 1.8e-7, "max": 2e-6}
  ],
  "metric_targets": [
    {"name": "vm", "direction": "target", "target_value": 0.9},
    {"name": "tpd", "direction": "min", "target_value": 2e-10}
  ]
}
```

AIDE converts that runtime intent into a `DynamicSpec`, which the optimizer and result parser can consume without knowing whether the circuit is an inverter, full adder, ring oscillator, or another supported SPICE design.

---

## 🗂️ Repository Structure

```text
AIDE/
├── copilot.py                 # Main prompt-driven Copilot entry point
├── core/
│   ├── dynamic_spec.py        # Runtime generic design specification
│   ├── dynamic_result_parser.py
│   ├── netlist_editor.py
│   ├── result_parser.py       # Legacy/static parser
│   ├── sim_executor.py
│   └── spec_checker.py        # Legacy/static spec loader/scorer
├── engines/
│   ├── circuit_generator.py   # LLM intent + SPICE generation
│   ├── bayes_opt.py            # Optuna optimizer
│   ├── llm_agent.py            # LLM optimizer
│   ├── netlist_validator.py
│   └── semantic_validator.py
├── reliability/
│   └── corner_stage.py         # PVT + Monte Carlo stage
├── prompts/
│   └── spice_blocks.md         # SPICE building-block context
├── templates/                  # Legacy inverter templates
├── spec.yaml                   # Legacy/manual inverter workflow configuration
├── orchestrator.py              # Legacy/manual orchestration path
├── report.py                   # Post-run visualization script
├── screenshots/                # Project demonstration screenshots
└── tests/                      # Offline unit tests
```

The repository currently contains both the newer prompt-driven Copilot architecture and an older configuration-driven inverter orchestration path. The sections above describe the **prompt-driven `copilot.py` workflow**, which is the intended user-facing AIDE experience.

---

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- LTspice
- Google Gemini API key

### Install

```bash
git clone https://github.com/karann0077/AIDE.git
cd AIDE
pip install -r requirements.txt
```

Create your environment file:

```bash
cp .env.example .env
```

Then configure:

```bash
GOOGLE_API_KEY=your_key_here
```

### Run a design request

```bash
python copilot.py "Design a CMOS inverter with a 1.8 V supply, switching point near 0.5VDD, and propagation delay below 200 ps."
```

### Interactive mode

```bash
python copilot.py
```

You can then enter successive design requests in the terminal.

### Use the LLM sizing engine

```bash
python copilot.py --engine llm "Design a CMOS inverter at 1.8 V with delay below 200 ps."
```

### Enable reliability analysis

```bash
python copilot.py --with-reliability "Design a CMOS inverter at 1.8 V with delay below 200 ps."
```

> **Note:** `--with-reliability` is the intended interface for the reliability stage; the repository also retains an older standalone `orchestrator.py` path for the original spec-driven workflow.

---

## ⚙️ The Role of `spec.yaml`

`spec.yaml` is retained in the repository as the original/manual inverter configuration used by `orchestrator.py`, `core/spec_checker.py`, and related legacy tests.

It defines items such as:

- inverter-specific design-variable bounds
- nominal performance targets
- PVT settings
- optimization budget
- reporting configuration

For the newer `copilot.py` prompt-driven workflow, the circuit-specific specification is generated dynamically by the LLM and represented by `DynamicSpec`. A generated snapshot is also saved as `output/<circuit_name>/spec_auto.yaml` for inspection and reproducibility.

This separation allows the repository to preserve the original deterministic inverter workflow without making the user manually edit `spec.yaml` for every new natural-language design request.

---

## 🔬 Optimization Engines

### `--engine bayes`

The default path uses Optuna's TPE sampler. It treats each transistor/device parameter as a search dimension, evaluates candidates using real LTspice measurements, and feeds the resulting objective back into the study.

### `--engine llm`

The alternative decision engine gives an LLM the current design variables, physical descriptions, target metrics, bounds, and recent simulation history. It proposes one structured candidate at a time.

In both modes:

> **The optimizer proposes; LTspice measures.**

The optimizer or LLM is never the final authority on circuit behavior.

---

## 🧪 Reliability Validation

The reliability stage is intended to evaluate the winning nominal candidate rather than every optimization trial.

Conceptually:

```text
Best nominal candidate
        ↓
   ┌────┴────┐
   ↓         ↓
Monte Carlo  PVT corners
   ↓         ↓
statistics  worst case
   └────┬────┘
        ↓
   robustness decision
```

The existing reliability implementation supports statistical variation analysis and a temperature × supply-voltage corner sweep, with the resulting data recorded alongside the optimization history.

---

## 🛡️ Robustness & Edge Cases

AIDE explicitly handles several practical automation problems:

| Failure / Edge Case | Handling |
|---|---|
| LLM returns invalid JSON | Parsing failure falls back to a safe candidate path in the optimization engine |
| LLM proposes out-of-range sizing | Candidate values are clamped to configured variable bounds |
| Generated SPICE fails in LTspice | Self-healing validation sends the error back to the LLM and retries |
| Required measurements missing | Validation rejects the candidate netlist |
| LTspice simulation failure/divergence | Candidate is treated as failed and assigned a large optimization penalty |
| macOS LTspice logs use UTF-16 | Parsers detect/decode the platform-specific log format |
| Derived metrics such as tpd | Computed deterministically from measured quantities when required |

---

## 🧪 Testing

The repository includes offline tests for the dynamic specification, scoring, candidate formatting, and result parsing. These tests do not require an active LTspice session or an API key.

```bash
python -m pytest tests/ -v
```

---

## 🎯 Why This Is Different

Traditional analog sizing often looks like:

```text
Specification
     ↓
Manual netlist/schematic changes
     ↓
LTspice simulation
     ↓
Manual measurement
     ↓
Manual transistor resizing
     ↓
Repeat
```

AIDE closes that loop:

```text
Natural-language request
          ↓
       LLM agent
          ↓
Dynamic design specification
          ↓
   SPICE generation
          ↓
LTspice validation/simulation
          ↓
  Automated measurement
          ↓
 Bayesian / LLM optimization
          ↓
 PVT + Monte Carlo validation
          ↓
      Final design
```

That makes AIDE less like a chatbot and more like a **closed-loop analog design-space exploration and automation framework around LTspice**.

---

## 📜 License

This project is licensed under the MIT License. See [LICENSE](LICENSE).

---

<div align="center">

**The agent interprets. The simulator measures. The optimizer searches. Reliability analysis validates.**

</div>
