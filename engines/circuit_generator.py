"""
engines/circuit_generator.py — LLM-based Circuit + Spec Generator
==================================================================
Takes a plain-English circuit description and produces:
  1. A validated DC SPICE netlist
  2. A validated TRAN SPICE netlist
  3. A DynamicSpec (design variables + metric targets)

Two LLM calls:
  Call #1 — Intent Parser:  NL → structured JSON intent
  Call #2 — Netlist Builder: intent JSON → SPICE netlists + meas + variables

Both calls use Gemini via google-generativeai.
"""
from __future__ import annotations

import json
import logging
import os
import re
import textwrap
from pathlib import Path

from core.dynamic_spec import DynamicSpec, MetricTarget

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load SPICE building blocks (injected into circuit generator prompt)
# ---------------------------------------------------------------------------
_BLOCKS_PATH = Path(__file__).parent.parent / "prompts" / "spice_blocks.md"
_SPICE_BLOCKS = _BLOCKS_PATH.read_text() if _BLOCKS_PATH.exists() else ""

# ---------------------------------------------------------------------------
# Model card (always appended to every generated netlist)
# ---------------------------------------------------------------------------
_MODEL_CARDS = """\
.model NMOS NMOS (LEVEL=3 TOX=4e-9 VTO=0.5 UO=450 THETA=0.1
+ KAPPA=0.3 ETA=0.01 NSUB=1e17 LD=5n WD=5n)
.model PMOS PMOS (LEVEL=3 TOX=4e-9 VTO=-0.5 UO=150 THETA=0.1
+ KAPPA=0.3 ETA=0.01 NSUB=1e17 LD=5n WD=5n)"""

# ---------------------------------------------------------------------------
# SYSTEM PROMPT — Intent Parser (Call #1)
# ---------------------------------------------------------------------------
_INTENT_SYSTEM = textwrap.dedent("""\
You are an expert analog/digital IC design engineer.
Extract structured design intent from a plain-English circuit description.

Output ONLY a valid JSON object — no markdown, no explanation, no code fences.

JSON schema:
{
  "circuit_name": "snake_case_name",
  "circuit_description": "one-sentence description",
  "topology": "brief description of circuit architecture",
  "vdd": <float, supply voltage in volts>,
  "design_variables": [
    {
      "name": "snake_case_param_name",
      "description": "what this controls",
      "min": <float, SI units>,
      "max": <float, SI units>,
      "init": <float, SI units>,
      "unit": "m"
    }
  ],
  "metric_targets": [
    {
      "name": "metric_label_matching_meas_statement",
      "source": "dc" or "tran" or "derived",
      "direction": "min" or "max" or "target",
      "target_value": <float, SI units>,
      "tolerance": <float 0-1, only for direction=target>,
      "weight": <float, importance>,
      "formula": "python expr using other metric names, or empty string"
    }
  ],
  "meas_dc":   ["list", "of", ".meas", "names", "from", "DC", "sim"],
  "meas_tran": ["list", "of", ".meas", "names", "from", "TRAN", "sim"],
  "corners": {"temp_c": [-40, 27, 125], "vdd_pct": [-10, 0, 10]}
}

Rules:
- Use SI units throughout (meters for widths/lengths, seconds for time, volts, amps, watts)
- NMOS widths typically 0.2e-6 to 20e-6 m; PMOS 0.4e-6 to 40e-6 m; lengths 180e-9 to 2e-6 m
- metric names must be valid SPICE identifiers (letters/digits/underscore only)
- meas_dc lists metrics measured in DC sweep; meas_tran for transient
- For tpd: use tphl_X and tplh_X in meas_tran, then tpd_X as a derived metric
  with formula "(abs(tphl_X)+abs(tplh_X))/2"
- Keep design_variables to 4-8 variables (optimizer works best with fewer)
""")

# ---------------------------------------------------------------------------
# SYSTEM PROMPT — Netlist Builder (Call #2)
# ---------------------------------------------------------------------------
_NETLIST_SYSTEM = textwrap.dedent(f"""\
You are an expert SPICE netlist writer for LTspice XVII on macOS.

CRITICAL RULES (violating any causes simulation failure):
1. DC and TRAN must be SEPARATE netlists — never mix in one file
2. Do NOT use curly braces {{}} in .meas PARAM= expressions — use single quotes instead
3. Use ONLY the LEVEL=3 MOSFET model cards provided below — no other models
4. ALL tunable values must be .param statements at the top
5. Output ONLY valid JSON with exactly these keys: netlist_dc, netlist_tran
6. .meas TRAN statements cannot reference other .meas values with PARAM — compute in Python
7. Every subcircuit instance line: X<name> <nodes> <subckt_name> [params]
8. VDD source: VDD vdd 0 DC <vdd_value>
9. All node names: lowercase, no spaces

MOSFET MODEL CARDS (always include at bottom of every netlist):
{_MODEL_CARDS}

AVAILABLE BUILDING BLOCKS (use these; write transistors directly for simple circuits):
{_SPICE_BLOCKS}

You will receive a JSON intent object. Generate TWO netlists.
""")

# ---------------------------------------------------------------------------
# Gemini API caller
# ---------------------------------------------------------------------------
def _call_gemini(system: str, user: str, temperature: float = 0.2) -> str:
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    resp = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            response_mime_type="application/json",
        ),
    )
    return resp.text


# ---------------------------------------------------------------------------
# JSON extraction helper (handles LLM wrapping in code fences)
# ---------------------------------------------------------------------------
def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------
class CircuitGenerator:
    """
    Turns a plain-English circuit description into:
      - Two SPICE netlists (DC + TRAN)
      - A DynamicSpec (design variables + metric targets)
    """

    def __init__(self, output_dir: str | Path = "output") -> None:
        self.output_dir = Path(output_dir)

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #
    def generate(
        self,
        prompt: str,
        chat_history: list[dict] | None = None,
    ) -> tuple[Path, Path, DynamicSpec]:
        """
        Generate validated netlists + DynamicSpec from a user prompt.

        Args:
            prompt: plain-English circuit description
            chat_history: previous turns for context (chat mode)

        Returns:
            (dc_path, tran_path, dynamic_spec)
        """
        logger.info("Intent parsing...")
        intent = self._parse_intent(prompt, chat_history)
        logger.info("Circuit: %s  |  Variables: %d  |  Targets: %d",
                    intent["circuit_name"],
                    len(intent["design_variables"]),
                    len(intent["metric_targets"]))

        logger.info("Generating SPICE netlists...")
        dc_text, tran_text = self._generate_netlists(intent)

        # Write to output directory
        circuit_dir = self.output_dir / intent["circuit_name"] / "templates"
        circuit_dir.mkdir(parents=True, exist_ok=True)
        dc_path   = circuit_dir / f"{intent['circuit_name']}_dc.cir"
        tran_path = circuit_dir / f"{intent['circuit_name']}_tran.cir"
        dc_path.write_text(dc_text)
        tran_path.write_text(tran_text)
        logger.info("Netlists written: %s, %s", dc_path, tran_path)

        # Build DynamicSpec
        spec = self._build_spec(intent, dc_path, tran_path)

        # Save auto-generated spec YAML for user inspection
        spec_yaml = self.output_dir / intent["circuit_name"] / "spec_auto.yaml"
        spec.to_yaml(spec_yaml)
        logger.info("Spec saved: %s", spec_yaml)

        return dc_path, tran_path, spec

    # ------------------------------------------------------------------ #
    # Call #1 — Intent Parser
    # ------------------------------------------------------------------ #
    def _parse_intent(
        self,
        prompt: str,
        chat_history: list[dict] | None,
    ) -> dict:
        context = ""
        if chat_history:
            context = "Previous conversation context:\n"
            for turn in chat_history[-3:]:          # last 3 turns
                context += f"User: {turn.get('user', '')}\n"
                context += f"Result: {turn.get('result_summary', '')}\n"
            context += "\nNew request:\n"

        user_msg = context + f"Circuit description: {prompt}"

        raw = _call_gemini(_INTENT_SYSTEM, user_msg, temperature=0.1)
        intent = _parse_json(raw)
        logger.debug("Intent: %s", json.dumps(intent, indent=2))
        return intent

    # ------------------------------------------------------------------ #
    # Call #2 — Netlist Builder
    # ------------------------------------------------------------------ #
    def _generate_netlists(self, intent: dict) -> tuple[str, str]:
        vdd = intent.get("vdd", 1.8)
        user_msg = textwrap.dedent(f"""\
            Generate DC and TRAN netlists for this circuit.

            Intent JSON:
            {json.dumps(intent, indent=2)}

            Requirements:
            - VDD = {vdd} V
            - Include all .param lines from design_variables at top
            - DC netlist: include ONLY .DC analysis + .meas for meas_dc metrics
            - TRAN netlist: include ONLY .TRAN analysis + .meas for meas_tran metrics
            - Use a realistic input stimulus (pulse/sine appropriate for the circuit)
            - Include load capacitor (10f) on output nodes
            - Append model cards at the very bottom of each netlist
            - DC .meas format: .meas DC name FIND V(node) WHEN condition
            - TRAN .meas format: .meas TRAN name TRIG ... TARG ...
            - DO NOT include .meas PARAM lines (Python computes derived metrics)

            Respond with JSON: {{"netlist_dc": "...full netlist text...", "netlist_tran": "...full netlist text..."}}
        """)

        raw = _call_gemini(_NETLIST_SYSTEM, user_msg, temperature=0.15)
        data = _parse_json(raw)

        dc_text   = data.get("netlist_dc", "")
        tran_text = data.get("netlist_tran", "")

        # Ensure model cards are present
        for name, text in [("DC", dc_text), ("TRAN", tran_text)]:
            if "LEVEL=3" not in text:
                logger.warning("%s netlist missing model cards — appending", name)

        if not dc_text:
            raise ValueError("LLM returned empty DC netlist")
        if not tran_text:
            raise ValueError("LLM returned empty TRAN netlist")

        return dc_text, tran_text

    # ------------------------------------------------------------------ #
    # Build DynamicSpec from intent JSON
    # ------------------------------------------------------------------ #
    def _build_spec(
        self,
        intent: dict,
        dc_path: Path,
        tran_path: Path,
    ) -> DynamicSpec:
        dv: dict[str, dict] = {}
        for v in intent.get("design_variables", []):
            dv[v["name"]] = {
                "min":  v["min"],
                "max":  v["max"],
                "init": v.get("init", (v["min"] + v["max"]) / 2),
                "unit": v.get("unit", "m"),
            }

        targets = []
        for mt in intent.get("metric_targets", []):
            targets.append(MetricTarget(
                name=mt["name"],
                source=mt.get("source", "tran"),
                direction=mt.get("direction", "min"),
                target_value=float(mt["target_value"]),
                tolerance=float(mt.get("tolerance", 0.05)),
                weight=float(mt.get("weight", 1.0)),
                formula=mt.get("formula", ""),
            ))

        return DynamicSpec(
            circuit_name=intent["circuit_name"],
            circuit_description=intent.get("circuit_description", intent["circuit_name"]),
            template_dc=dc_path,
            template_tran=tran_path,
            design_variables=dv,
            metric_targets=targets,
            meas_dc=intent.get("meas_dc", []),
            meas_tran=intent.get("meas_tran", []),
            corners=intent.get("corners", {
                "temp_c": [-40, 27, 125],
                "vdd_pct": [-10, 0, 10],
                "tolerance_pct": 10,
            }),
        )
