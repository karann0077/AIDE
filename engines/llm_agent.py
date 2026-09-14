"""
engines/llm_agent.py — LLM-based Decision Engine
=================================================
Sends the spec (as natural language) + last-k iterations to an LLM
and asks it to propose the next sizing as structured JSON.

Supports three API providers:
  - google   (google-generativeai / Gemini)
  - openai   (openai)
  - anthropic (anthropic)

The LLM NEVER touches the netlist directly.  Its JSON response is
validated, clamped to hard physical limits, and only then forwarded
to the Netlist Editor.  If JSON parsing fails we fall back to a
random point in the search space (with a logged warning).
"""
from __future__ import annotations

import json
import logging
import random
from typing import Any

from engines.base import Candidate, Iteration

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt (injected once per session)
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """You are an expert circuit-design optimization agent. You are operating
inside an automated LTspice design loop.

Your task is to propose the SINGLE most promising next candidate based on:
1. the available design variables,
2. their physical meaning and bounds,
3. the measured circuit metrics,
4. the target constraints,
5. the history of previous simulation results.

Rules:
1. Respond with ONLY valid JSON.
2. JSON schema:
   {
     "values": {
       "<variable_name>": <float in SI units>,
       ...
     },
     "reason": "<one concise engineering explanation>"
   }
3. Use ONLY variables present in the supplied specification.
4. NEVER invent variable names.
5. Stay within hard bounds.
6. Identify the metric(s) currently violating constraints.
7. Prefer changes to variables whose descriptions are physically related to the failing metric.
8. Avoid changing every variable at once unless the evidence supports it.
9. Do not assume the circuit is an inverter.
10. Do not assume metrics are Vm/tpd; use the supplied metric definitions.
"""

# ---------------------------------------------------------------------------
# Helper: build the user prompt for each iteration call
# ---------------------------------------------------------------------------
def _build_prompt(spec: Any, history: list[Iteration], window: int) -> str:
    dv = spec.design_variables
    
    # Inject variable semantics
    bounds_lines = []
    for k, v in dv.items():
        desc = v.get("description", "No description")
        role = v.get("role", "Unknown role")
        unit = v.get("unit", "")
        init = v["init"]
        bounds_lines.append(
            f"  {k} ({desc}, role: {role}): [{v['min']:.3g}, {v['max']:.3g}] {unit} "
            f"(init: {init:.3g})"
        )
    bounds_txt = "\n".join(bounds_lines)

    # Inject metric semantics
    targets_txt = "Targets:\n"
    for m in spec.metrics:
        if m.target is not None:
            op_str = "min" if m.objective == "min" else "max" if m.objective == "max" else "target"
            targets_txt += f"  {m.name} ({m.objective}): {op_str} {m.target} (source: {m.source})\n"

    recent = history[-window:] if len(history) >= window else history
    if recent:
        hist_lines = []
        for it in recent:
            m = it.metrics
            var_str = " ".join(f"{k}={v:.4g}" for k, v in it.candidate.values.items())
            met_str = " ".join(f"{k}={v:.4g}" for k, v in m.items() if v is not None)
            
            hist_lines.append(
                f"  iter={it.iteration}  |  VARS: {var_str}  |  METRICS: {met_str}  |  "
                f"error={it.error:.4f}  {'PASS' if it.passed else 'FAIL'}"
                + (f"  [reason: {it.candidate.reason}]" if it.candidate.reason else "")
            )
        history_txt = "\n".join(hist_lines)
    else:
        history_txt = "  (no iterations yet — start from initial values)"

    return (
        f"{targets_txt}\n\n"
        f"Design variable semantics and bounds:\n{bounds_txt}\n\n"
        f"Last {len(recent)} iteration(s):\n{history_txt}\n\n"
        "Propose the next sizing."
    )


# ---------------------------------------------------------------------------
# Provider-specific API wrappers
# ---------------------------------------------------------------------------
def _call_google(model: str, system: str, user: str, temperature: float) -> str:
    from google import genai
    from google.genai import types
    import os

    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    resp = client.models.generate_content(
        model=model,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            response_mime_type="application/json",
        ),
    )
    return resp.text


def _call_openai(model: str, system: str, user: str, temperature: float) -> str:
    from openai import OpenAI  # type: ignore
    import os

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content


def _call_anthropic(model: str, system: str, user: str, temperature: float) -> str:
    import anthropic  # type: ignore
    import os

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model=model,
        max_tokens=512,
        system=system,
        messages=[{"role": "user", "content": user}],
        temperature=temperature,
    )
    return msg.content[0].text


_PROVIDERS = {
    "google": _call_google,
    "openai": _call_openai,
    "anthropic": _call_anthropic,
}


# ---------------------------------------------------------------------------
# Main engine class
# ---------------------------------------------------------------------------
class LLMAgentEngine:
    """
    LLM-backed decision engine.  Requires an API key in the environment:
      - GOOGLE_API_KEY   (for google / Gemini)
      - OPENAI_API_KEY   (for openai / GPT)
      - ANTHROPIC_API_KEY (for anthropic / Claude)
    """

    def __init__(self, spec: Any) -> None:
        self.spec = spec
        self._provider = spec.llm["api_provider"]
        self._model = spec.llm["model"]
        self._window = spec.llm.get("history_window", 5)
        self._temperature = spec.llm.get("temperature", 0.3)

        if self._provider not in _PROVIDERS:
            raise ValueError(
                f"Unknown LLM provider '{self._provider}'. "
                f"Choose from {list(_PROVIDERS)}"
            )

    # ------------------------------------------------------------------
    def warm_start(self, initial_values: dict[str, float]) -> None:
        """No-op for LLM engine — history serves as warm-start."""
        logger.info("LLMAgentEngine: warm-start noted (history will seed context).")

    # ------------------------------------------------------------------
    def propose_next(
        self, history: list[Iteration], spec: Any
    ) -> list[Candidate]:
        """Call the LLM, parse JSON, clamp values, return one Candidate."""
        user_prompt = _build_prompt(spec, history, self._window)
        call_fn = _PROVIDERS[self._provider]

        try:
            raw = call_fn(
                self._model, _SYSTEM_PROMPT, user_prompt, self._temperature
            )
            logger.debug("LLM raw response: %s", raw)
            data = json.loads(raw)
            raw_values: dict[str, float] = data["values"]
            reason: str = data.get("reason", "")
        except Exception as exc:
            logger.warning(
                "LLM call/parse failed (%s). Falling back to random point.", exc
            )
            raw_values = self._random_point()
            reason = f"LLM fallback (random) — error: {exc}"

        # Hard clamp to physical limits — NEVER trust agent output blindly
        values = self._clamp(raw_values)
        logger.info("LLM proposes: %s  |  reason: %s", values, reason)

        run_id = len(history)
        return [Candidate(values=values, run_id=run_id, reason=reason)]

    # ------------------------------------------------------------------
    def _clamp(self, raw: dict[str, float]) -> dict[str, float]:
        """Clamp each proposed value to the bounds in spec.design_variables."""
        clamped: dict[str, float] = {}
        for var, cfg in self.spec.design_variables.items():
            v = raw.get(var, cfg["init"])
            clamped[var] = max(cfg["min"], min(cfg["max"], float(v)))
            if v != clamped[var]:
                logger.warning(
                    "Clamped %s from %.4g → %.4g", var, v, clamped[var]
                )
        return clamped

    def _random_point(self) -> dict[str, float]:
        """Random point in the search space (fallback when LLM fails)."""
        return {
            k: random.uniform(v["min"], v["max"])
            for k, v in self.spec.design_variables.items()
        }
