"""
engines/netlist_validator.py — LTspice Validation + Self-Healing
================================================================
Runs LTspice on a generated netlist and checks for success.
If it fails, feeds the error back to the LLM and retries up to
MAX_RETRIES times.

This is the critical safety layer that ensures the optimizer
never receives a netlist that LTspice cannot run.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import textwrap
import time
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
TIMEOUT_S = 60.0


class NetlistValidationError(Exception):
    """Raised when a netlist fails validation after all retries."""
    pass


class NetlistValidator:
    """
    Validate generated netlists against real LTspice.

    Usage:
        validator = NetlistValidator()
        dc_path, tran_path = validator.validate_and_fix(dc_text, tran_text, intent)
    """

    def __init__(self, work_dir: str | Path = "/tmp/aide_validate") -> None:
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def validate_and_fix(
        self,
        dc_text: str,
        tran_text: str,
        intent: dict,
    ) -> tuple[str, str]:
        """
        Validate both netlists. If either fails, call LLM to fix and retry.

        Returns: (fixed_dc_text, fixed_tran_text)
        Raises:  NetlistValidationError after MAX_RETRIES failures
        """
        for attempt in range(1, MAX_RETRIES + 1):
            logger.info("Validation attempt %d/%d ...", attempt, MAX_RETRIES)

            dc_err   = self._run_one(dc_text,   "dc",   attempt, intent)
            tran_err = self._run_one(tran_text, "tran", attempt, intent)

            if dc_err is None and tran_err is None:
                logger.info("✓ Both netlists validated successfully")
                return dc_text, tran_text

            # Build error report for LLM
            errors = []
            if dc_err:
                errors.append(f"DC netlist error:\n{dc_err}")
            if tran_err:
                errors.append(f"TRAN netlist error:\n{tran_err}")

            error_report = "\n\n".join(errors)
            logger.warning("Validation failed:\n%s", error_report)

            if attempt < MAX_RETRIES:
                logger.info("Asking LLM to fix errors (attempt %d)...", attempt)
                dc_text, tran_text = self._llm_fix(
                    dc_text, tran_text, error_report, intent
                )
            else:
                raise NetlistValidationError(
                    f"Netlist validation failed after {MAX_RETRIES} attempts.\n"
                    f"Last errors:\n{error_report}"
                )

        raise NetlistValidationError("Unexpected exit from retry loop")

    # ------------------------------------------------------------------ #
    def _run_one(self, netlist_text: str, suffix: str, attempt: int, intent: dict) -> str | None:
        """
        Write netlist to temp file, run LTspice, return error string or None.
        Now validates execution result and required measurements.
        """
        from PyLTSpice import LTspice
        import math
        from core.result_parser import extract_metrics

        path = self.work_dir / f"validate_{suffix}_a{attempt}.cir"
        path.write_text(netlist_text)

        try:
            import subprocess
            import sys
            exe = LTspice.spice_exe[0] if isinstance(LTspice.spice_exe, list) and LTspice.spice_exe else LTspice.spice_exe
            cmd = [exe, "-b", "-Run", str(path.resolve())] if sys.platform == "win32" else [exe, "-b", str(path.resolve())]
            
            result = subprocess.run(
                cmd,
                timeout=TIMEOUT_S,
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired:
            return f"LTspice timed out after {TIMEOUT_S}s"
        except Exception as exc:
            return f"Failed to launch LTspice: {exc}"

        raw_path = path.with_suffix(".raw")
        stem1    = path.with_name(path.stem + "_1.raw")
        log_path = path.with_suffix(".log")
        log1     = path.with_name(path.stem + "_1.log")
        
        has_raw = raw_path.exists() or stem1.exists()
        lp = log1 if log1.exists() else log_path

        # Even if raw exists, check if process failed
        log_errors = []
        if lp.exists():
            raw_bytes = lp.read_bytes()
            try:
                log_text = raw_bytes.decode("utf-16-le", errors="strict")
            except Exception:
                log_text = raw_bytes.decode("utf-8", errors="replace")
            # Extract error lines
            log_errors = [
                line.strip() for line in log_text.splitlines()
                if any(kw in line.lower() for kw in
                       ("error", "fatal", "undefined", "questionable", "aborted"))
            ]
            
        if result.returncode != 0 or not has_raw or log_errors:
            err_msg = []
            if result.returncode != 0:
                err_msg.append(f"LTspice execution failed with return code {result.returncode}")
            if not has_raw:
                err_msg.append("No .raw file produced.")
            if log_errors:
                err_msg.append("Log errors:\n" + "\n".join(log_errors[:10]))
            if result.stderr:
                err_msg.append("Stderr:\n" + result.stderr.strip()[:500])
            return "\n\n".join(err_msg)

        # Check measurements semantics
        if suffix == "dc":
            req_meas = set(intent.get("meas_dc", []))
            metrics = extract_metrics(dc_log=lp, tran_log=None)
        else:
            req_meas = set(intent.get("meas_tran", []))
            metrics = extract_metrics(dc_log=None, tran_log=lp)
            
        meas_errors = []
        for name in req_meas:
            val = metrics.get(name)
            if val is None or not math.isfinite(val):
                meas_errors.append(f"Measurement '{name}' is missing or non-finite: {val}")
                
        if meas_errors:
            return "Measurement validation failed:\n" + "\n".join(meas_errors)

        return None

    # ------------------------------------------------------------------ #
    def _llm_fix(
        self,
        dc_text: str,
        tran_text: str,
        error_report: str,
        intent: dict,
    ) -> tuple[str, str]:
        """Ask Gemini to fix the netlist errors."""
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

        system = textwrap.dedent("""\
            You are an expert SPICE debugger for LTspice XVII on macOS.
            Fix the provided netlists to eliminate the LTspice errors.

            CRITICAL RULES:
            1. DC and TRAN must remain separate netlists
            2. No curly braces {} in .meas PARAM expressions
            3. Use only LEVEL=3 MOSFET models
            4. Keep all .param names identical — only fix syntax/structure
            5. Output ONLY JSON: {"netlist_dc": "...", "netlist_tran": "..."}
        """)

        user = textwrap.dedent(f"""\
            LTspice reported these errors:
            {error_report}

            Current DC netlist:
            {dc_text}

            Current TRAN netlist:
            {tran_text}

            Fix all errors and return corrected netlists as JSON.
        """)

        resp = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.1,
                response_mime_type="application/json",
            ),
        )
        raw = resp.text.strip()
        raw = re.sub(r"^```(?:json)?\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)

        try:
            data = json.loads(raw)
            return data["netlist_dc"], data["netlist_tran"]
        except Exception as exc:
            logger.error("LLM fix parse failed: %s", exc)
            return dc_text, tran_text   # return originals, will fail again
