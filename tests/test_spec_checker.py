"""
tests/test_spec_checker.py — Unit tests for the Spec Checker
(No simulator dependency — runs completely offline)
"""
import math
import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.spec_checker import load_spec, score, verdict_str, Spec

SPEC_PATH = Path(__file__).parent.parent / "spec.yaml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def spec() -> Spec:
    return load_spec(SPEC_PATH)


# ---------------------------------------------------------------------------
# load_spec tests
# ---------------------------------------------------------------------------
class TestLoadSpec:
    def test_loads_without_error(self, spec):
        assert spec is not None

    def test_circuit_name(self, spec):
        assert "Inverter" in spec.circuit_name

    def test_vm_target_is_half_vdd(self, spec):
        assert math.isclose(spec.vm_target, spec.target["vdd_nominal"] / 2, rel_tol=0.01)

    def test_design_variables_have_bounds(self, spec):
        for var, cfg in spec.design_variables.items():
            assert cfg["min"] < cfg["max"], f"{var}: min >= max"
            assert cfg["min"] <= cfg["init"] <= cfg["max"], f"{var}: init out of bounds"

    def test_tpd_max_in_seconds(self, spec):
        # tpd_max should be in nanoseconds in YAML, property converts to seconds
        assert spec.tpd_max < 1e-7          # 200 ps = 2e-10 s, well below 100 ns
        assert spec.tpd_max > 1e-12         # above 1 ps (sanity)


# ---------------------------------------------------------------------------
# score() tests
# ---------------------------------------------------------------------------
class TestScore:
    def test_perfect_spec_passes(self, spec):
        metrics = {"vm": spec.vm_target, "tpd": spec.tpd_max * 0.5}
        passed, err = score(metrics, spec)
        assert passed
        assert err == pytest.approx(0.0, abs=1e-9)

    def test_vm_too_high_fails(self, spec):
        metrics = {"vm": spec.vm_target * 1.2, "tpd": spec.tpd_max * 0.5}
        passed, err = score(metrics, spec)
        assert not passed
        assert err > 0

    def test_tpd_too_slow_fails(self, spec):
        metrics = {"vm": spec.vm_target, "tpd": spec.tpd_max * 1.5}
        passed, err = score(metrics, spec)
        assert not passed
        assert err > 0

    def test_both_fail_gives_higher_error(self, spec):
        metrics_both = {"vm": spec.vm_target * 1.2, "tpd": spec.tpd_max * 1.5}
        metrics_one  = {"vm": spec.vm_target * 1.2, "tpd": spec.tpd_max * 0.5}
        _, err_both = score(metrics_both, spec)
        _, err_one  = score(metrics_one, spec)
        assert err_both > err_one

    def test_nan_metrics_gives_large_error(self, spec):
        metrics = {"vm": float("nan"), "tpd": float("nan")}
        passed, err = score(metrics, spec)
        assert not passed
        assert err > 100

    def test_tpd_at_limit_passes(self, spec):
        metrics = {"vm": spec.vm_target, "tpd": spec.tpd_max}
        passed, _ = score(metrics, spec)
        assert passed


# ---------------------------------------------------------------------------
# verdict_str tests
# ---------------------------------------------------------------------------
class TestVerdictStr:
    def test_pass_contains_checkmark(self, spec):
        metrics = {"vm": spec.vm_target, "tpd": spec.tpd_max * 0.5}
        v = verdict_str(metrics, spec)
        assert "PASS" in v

    def test_fail_contains_x(self, spec):
        metrics = {"vm": spec.vm_target * 1.5, "tpd": spec.tpd_max * 2}
        v = verdict_str(metrics, spec)
        assert "FAIL" in v
