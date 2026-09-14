"""
core/dynamic_result_parser.py — Generic .meas log parser
=========================================================
Reads LTspice DC and TRAN logs and extracts ANY named .meas value.
The caller tells us which names to look for (from DynamicSpec).

Also computes derived metrics (e.g. tpd = avg of tphl + tplh) when
a formula is specified in DynamicSpec.metric_targets.

LTspice Mac log files are UTF-16-LE encoded.
Two .meas output formats exist:
  DC:   "vm: v(out)=0.850484 at ..."    → NAME: EXPR=VALUE
  TRAN: "tphl=4.95e-12 FROM ... TO ..."  → NAME=VALUE
"""
from __future__ import annotations

import logging
import math
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Format-specific regexes
# ---------------------------------------------------------------------------
# DC format:   "name: expr=value [rest]"
_DC_RE = re.compile(
    r"^(?P<name>\w+):\s+\S+=(?P<value>[+-]?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)",
    re.IGNORECASE | re.MULTILINE,
)

# TRAN format: "name=value [FROM ...]"
_TRAN_RE = re.compile(
    r"^(?P<name>\w+)=(?P<value>[+-]?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)",
    re.IGNORECASE | re.MULTILINE,
)

# Fallback: any "name=value" anywhere in the log
_FALLBACK_RE = re.compile(
    r"(?P<name>\w+)\s*=\s*(?P<value>[+-]?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)",
    re.IGNORECASE | re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Log reader — handles UTF-16-LE (LTspice Mac) and UTF-8
# ---------------------------------------------------------------------------
def _read_log(path: Optional[Path]) -> str:
    if path is None or not path.exists():
        return ""
    raw = path.read_bytes()
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-16-le", errors="replace")
    except Exception:
        return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Core extractor
# ---------------------------------------------------------------------------
def _extract(text: str, wanted: list[str], primary_re: re.Pattern) -> dict[str, float]:
    """Extract wanted metric names from log text using primary regex."""
    result = {k: float("nan") for k in wanted}
    if not text:
        return result

    # Primary regex
    for m in primary_re.finditer(text):
        name = m.group("name").lower()
        if name in result:
            try:
                result[name] = float(m.group("value"))
            except ValueError:
                pass

    # Fallback for any still-NaN metrics
    missing = [k for k, v in result.items() if math.isnan(v)]
    if missing:
        for m in _FALLBACK_RE.finditer(text):
            name = m.group("name").lower()
            if name in missing:
                try:
                    result[name] = float(m.group("value"))
                except ValueError:
                    pass

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def parse_dc_log(log_path: Optional[Path], wanted: list[str]) -> dict[str, float]:
    """Extract named metrics from a DC sweep log."""
    text = _read_log(log_path)
    result = _extract(text, [w.lower() for w in wanted], _DC_RE)
    logger.debug("DC log %s → %s", log_path, result)
    return result


def parse_tran_log(log_path: Optional[Path], wanted: list[str]) -> dict[str, float]:
    """Extract named metrics from a TRAN log."""
    text = _read_log(log_path)
    result = _extract(text, [w.lower() for w in wanted], _TRAN_RE)
    logger.debug("TRAN log %s → %s", log_path, result)
    return result


def extract_metrics(
    dc_log:   Optional[Path],
    tran_log: Optional[Path],
    meas_dc:   list[str],
    meas_tran: list[str],
    dc_raw:   Optional[Path] = None,
    tran_raw: Optional[Path] = None,
) -> dict[str, float]:
    """
    Merge DC + TRAN metrics into one dict.

    Args:
        dc_log / tran_log: log file paths
        meas_dc / meas_tran: list of .meas label names to look for
        dc_raw / tran_raw: raw binary files (future: waveform extraction)

    Returns:
        Flat dict of {metric_name: float_value_in_SI_units}
        Missing/failed metrics are NaN.
    """
    dc_metrics   = parse_dc_log(dc_log,   meas_dc)
    tran_metrics = parse_tran_log(tran_log, meas_tran)
    merged = {**dc_metrics, **tran_metrics}

    # Auto-compute tpd variants from tphl/tplh pairs
    # Pattern: if we have tphl_X and tplh_X, compute tpd_X automatically
    for key in list(merged.keys()):
        if key.startswith("tphl"):
            suffix = key[4:]                     # e.g. "_sum"
            partner = "tplh" + suffix
            derived = "tpd" + suffix
            if partner in merged and derived not in merged:
                hl = merged[key]
                lh = merged[partner]
                if not math.isnan(hl) and not math.isnan(lh):
                    merged[derived] = (abs(hl) + abs(lh)) / 2.0

    return merged
