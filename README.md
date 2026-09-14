<div align="center">

# 🔬 AIDE — AI-Driven Analog Design Explorer

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

![Project Demo Placeholder](docs/demo_placeholder.png) *(Add a GIF or screenshot of the Copilot running here)*

**AIDE** is your automated **"Copilot for LTspice"**. Just describe the circuit you want in plain English, and AIDE handles the rest. It generates the SPICE code, validates it against LTspice, and automatically optimizes transistor sizes to meet your performance targets (like delay, power, and noise margins) using Bayesian Optimization or AI.

Say goodbye to manually tweaking widths and lengths and running endless trial-and-error simulations.

</div>

---

## 🌟 What This Project Does

AIDE automates the most tedious part of analog circuit design: sizing components.

Imagine you need a 6T SRAM cell with a read static noise margin (RSNM) of 250mV and leakage power under 1nW. Instead of guessing transistor sizes, you simply type that requirement into AIDE. 

1. **AI Generation:** The AI writes the initial SPICE netlist for the circuit.
2. **Self-Healing:** If the AI makes a SPICE syntax mistake, AIDE catches the LTspice error, feeds it back to the AI, and the AI fixes its own code.
3. **Headless Optimization:** AIDE sweeps through thousands of possible transistor sizes automatically, running headless LTspice simulations in the background.
4. **Reliability Testing:** Once it finds a working design, it rigorously tests it across 200 Monte Carlo statistical variations and extreme Process/Voltage/Temperature (PVT) corners.
5. **Final Report:** You get a fully sized, production-ready `.cir` file and a beautiful graphical report of its performance.

---

## 🚀 How to Run AIDE on Your Computer

Running AIDE is easy. Open your terminal (Bash) and follow these simple steps:

### 1. Prerequisites
You must have **LTspice** installed on your computer. (AIDE works on both Mac and Windows). You also need Python 3.10 or newer.

### 2. Download and Install
```bash
# Clone the repository to your computer
git clone https://github.com/karann0077/AIDE.git

# Enter the project folder
cd AIDE/agentic-ltspice

# Install the required Python packages
pip install -r requirements.txt
```

### 3. Add Your AI Key
AIDE uses Google's AI (Gemini) to write the code. You need a free API key from [Google AI Studio](https://aistudio.google.com/).
```bash
# Copy the example environment file
cp .env.example .env

# Open .env in a text editor and paste your Google API key:
# GOOGLE_API_KEY=your_key_here
```

### 4. Talk to the Copilot!
You can now ask the Copilot to design a circuit for you. 
```bash
python copilot.py "Design a 6T CMOS SRAM cell with a 1.8 V supply. Ensure reliable read and write operation, with a read static noise margin (RSNM) of at least 250 mV."
```

If you just run `python copilot.py` without any text, it will open an interactive chat where you can talk back and forth with the AI!

---

## 📸 See It In Action (Demo)

![SRAM Optimization Demo](docs/sram_optimization_demo.png) *(Add a picture of the terminal output showing the iteration process here)*

![Final Reliability Report](docs/reliability_report.png) *(Add a picture of the generated report.png showing the graphs here)*

AIDE outputs a beautiful 6-panel graph showing how it converged on the perfect transistor sizes, including the Monte Carlo yield histogram.

---

## 🧠 How It Works (In Simple Language)

Here is exactly what happens under the hood when you press enter:

1. **The Brain (LLM):** AIDE sends your prompt to Google Gemini. Gemini creates the initial SPICE file and defines a "Schema" (the variables it is allowed to change, like `w_pu` for pull-up width, and the target goals).
2. **The Validator:** AIDE runs this raw SPICE file in LTspice. If LTspice crashes, AIDE reads the red error text, sends it back to Gemini, and says "Fix this."
3. **The Optimizer (BayesOpt):** Once the file runs, AIDE hands the controls over to a mathematical engine called Optuna. Optuna intelligently guesses new sizes for the transistors, runs LTspice (which takes ~40 milliseconds), checks the results, and guesses again. It does this until the targets are met.
4. **The Stress Test:** The best design is subjected to a "Monte Carlo" simulation—running the circuit 200 times with tiny, randomized manufacturing defects injected into the transistors to ensure the design is robust enough for real-world silicon fabrication.

### Two Modes of Operation
- **`--engine bayes` (Default):** Uses pure math to find the right sizes. It is incredibly fast and highly reliable.
- **`--engine llm`:** Uses the AI to guess the sizes! The AI looks at the history of failed simulations and uses engineering logic to decide what to tweak next.

---

## 🛠️ Edge Cases Handled

AIDE is built to be robust. It won't crash just because the AI hallucinates.

| Problem | How AIDE Handles It |
|---|---|
| **LTspice crashes** | Detected via missing `.raw` files; scored as a massive failure so the optimizer avoids that sizing region. |
| **AI guesses crazy sizes** | All guesses are hard-clamped to realistic minimums and maximums (e.g., 200nm to 20µm) *before* reaching LTspice. |
| **Relative Path Crashes on Mac** | AIDE resolves all file paths to absolute paths to prevent the notorious macOS LTspice "code 255" crash. |
| **Mac Log Encoding** | macOS LTspice outputs weird `UTF-16-LE` text logs. AIDE detects the BOM and decodes it flawlessly. |

---

## 🧪 For Developers: Running the Tests

AIDE includes a suite of offline unit tests that don't require LTspice to be open.

```bash
python -m pytest tests/ -v
```

---

<div align="center">

Built as a proof-of-concept for agentic analog design automation.  
**The optimizer finds the numbers. The agent explains the reasoning. LTspice does the physics.**

</div>
