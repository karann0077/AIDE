"""
core/result_parser.py — Extract .meas results from LTspice logs
================================================================
LTspice on Mac writes UTF-16-LE encoded .log files.
This parser handles that encoding and merges DC + TRAN logs
into a single metrics dict.

Measurements extracted:
  DC log:   vm
  TRAN log: tphl, tplh
  Python:   tpd = (abs(tphl) + abs(tplh)) / 2
            (LTspice Mac cannot compute tpd via .meas PARAM)
"""
from __future__ import annotations

import logging
import math
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Metrics expected from each analysis type
_DC_METRICS   = {"vm"}
_TRAN_METRICS = {"tphl", "tplh"}

# LTspice Mac produces two distinct .meas output formats:
#
# DC:   "vm: v(out)=0.850484 at 0.850484"
#        ^name  ^label  ^VALUE
#
# TRAN: "tphl=4.95204e-12 FROM 1.025e-09 TO 1.02995e-09"
#        ^name ^VALUE
#
# We use two separate regexes to handle both:

# Matches "NAME: anything=VALUE" (DC format)
_DC_MEAS_RE = re.compile(
    r"^(?P<name>\w+):\s+\S+=(?P<value>[+-]?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)",
    re.IGNORECASE | re.MULTILINE,
)

# Matches "NAME=VALUE" at line start (TRAN format, ignores "FROM ...")
_TRAN_MEAS_RE = re.compile(
    r"^(?P<name>\w+)=(?P<value>[+-]?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)",
    re.IGNORECASE | re.MULTILINE,
)


def _read_log(log_path: Optional[Path]) -> str:
    """Read an LTspice log, handling UTF-16-LE encoding used on Mac deterministically."""
    if log_path is None or not log_path.exists():
        return ""
    raw = log_path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", errors="strict")
    # Heuristic for UTF-16 without BOM
    if b"\x00" in raw[:200]:
        try:
            return raw.decode("utf-16-le", errors="strict")
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")


def _parse_log(log_text: str, wanted: set[str], regex: re.Pattern) -> dict[str, float]:
    """Extract named .meas values from a decoded log string."""
    result: dict[str, float] = {k: float("nan") for k in wanted}
    for m in regex.finditer(log_text):
        name = m.group("name").lower()
        if name in wanted:
            try:
                result[name] = float(m.group("value"))
            except ValueError:
                pass
    return result


def parse_dc_log(log_path: Optional[Path]) -> dict[str, float]:
    """Extract Vm from the DC sweep log."""
    text = _read_log(log_path)
    if not text:
        logger.warning("DC log empty/missing: %s", log_path)
    result = _parse_log(text, _DC_METRICS, _DC_MEAS_RE)
    logger.debug("DC metrics from %s: %s", log_path, result)
    return result


def parse_tran_log(log_path: Optional[Path]) -> dict[str, float]:
    """Extract tphl, tplh from the TRAN log."""
    text = _read_log(log_path)
    if not text:
        logger.warning("TRAN log empty/missing: %s", log_path)
    result = _parse_log(text, _TRAN_METRICS, _TRAN_MEAS_RE)
    logger.debug("TRAN metrics from %s: %s", log_path, result)
    return result


def extract_metrics(
    dc_log: Optional[Path],
    tran_log: Optional[Path],
    dc_raw: Optional[Path] = None,
    tran_raw: Optional[Path] = None,
) -> dict[str, float]:
    """
    Merge DC + TRAN metrics into one dict.
    Computes tpd = (|tphl| + |tplh|) / 2 in Python.
    """
    dc_metrics   = parse_dc_log(dc_log)
    tran_metrics = parse_tran_log(tran_log)

    merged = {**dc_metrics, **tran_metrics}

    # Compute tpd in Python (LTspice Mac cannot do cross-.meas PARAM)
    tphl = merged.get("tphl", float("nan"))
    tplh = merged.get("tplh", float("nan"))
    if not math.isnan(tphl) and not math.isnan(tplh):
        merged["tpd"] = (abs(tphl) + abs(tplh)) / 2.0
    else:
        merged["tpd"] = float("nan")

    return merged


def parse_log(log_path: Path) -> dict[str, float]:
    """
    Parse an LTspice log and return all supported metrics.
    Attempts both DC and TRAN measurement formats and computes tpd from tphl/tplh when available.
    """
    metrics: dict[str, float] = {}
    if not log_path or not log_path.exists():
        logger.warning("Log file does not exist: %s", log_path)
        return metrics

    text = _read_log(log_path)
    
    # DC-style measurements
    for m in _DC_MEAS_RE.finditer(text):
        name = m.group("name").lower()
        try:
            metrics[name] = float(m.group("value"))
        except ValueError:
            continue

    # TRAN-style measurements
    for m in _TRAN_MEAS_RE.finditer(text):
        name = m.group("name").lower()
        try:
            metrics[name] = float(m.group("value"))
        except ValueError:
            continue

    # Derived propagation delay
    tphl = metrics.get("tphl")
    tplh = metrics.get("tplh")
    if tphl is not None and tplh is not None:
        metrics["tpd"] = (abs(tphl) + abs(tplh)) / 2.0
        
    return metrics
