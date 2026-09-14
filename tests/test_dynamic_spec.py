"""
tests/test_dynamic_spec.py — Unit tests for DynamicSpec
(No LTspice, no API key needed)
"""
import math
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.dynamic_spec import DynamicSpec, MetricTarget


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_spec(targets: list[MetricTarget]) -> DynamicSpec:
    return DynamicSpec(
        circuit_name="test_circuit",
        circuit_description="test",
        template_dc=Path("dc.cir"),
        template_tran=Path("tran.cir"),
        design_variables={"wn": {"min": 1e-6, "max": 10e-6, "init": 2e-6, "unit": "m"}},
        metric_targets=targets,
        meas_dc=[], meas_tran=[],
    )


# ---------------------------------------------------------------------------
# MetricTarget tests
# ---------------------------------------------------------------------------
class TestMetricTarget:
    def test_min_satisfied(self):
        mt = MetricTarget("tpd", "tran", "min", 200e-9)
        assert mt.is_satisfied(150e-9)
        assert not mt.is_satisfied(250e-9)

    def test_max_satisfied(self):
        mt = MetricTarget("gain", "dc", "max", 40.0)
        assert mt.is_satisfied(50.0)
        assert not mt.is_satisfied(30.0)

    def test_target_within_tolerance(self):
        mt = MetricTarget("vm", "dc", "target", 0.9, tolerance=0.05)
        assert mt.is_satisfied(0.9)
        assert mt.is_satisfied(0.94)   # +4.4%, within 5%
        assert not mt.is_satisfied(1.0)  # +11%, outside 5%

    def test_nan_is_not_satisfied(self):
        mt = MetricTarget("tpd", "tran", "min", 200e-9)
        assert not mt.is_satisfied(float("nan"))

    def test_error_at_target_is_zero(self):
        mt = MetricTarget("tpd", "tran", "min", 200e-9)
        assert mt.error(100e-9) == pytest.approx(0.0)   # below limit → no error

    def test_error_above_limit_positive(self):
        mt = MetricTarget("tpd", "tran", "min", 200e-9)
        err = mt.error(300e-9)
        assert err > 0

    def test_nan_gives_large_error(self):
        mt = MetricTarget("tpd", "tran", "min", 200e-9)
        assert mt.error(float("nan")) > 100


# ---------------------------------------------------------------------------
# DynamicSpec.score() tests
# ---------------------------------------------------------------------------
class TestDynamicSpecScore:
    def _inverter_spec(self) -> DynamicSpec:
        return _make_spec([
            MetricTarget("vm",  "dc",   "target", 0.9, tolerance=0.05),
            MetricTarget("tpd", "derived", "min", 200e-9),
        ])

    def test_all_targets_met_passes(self):
        spec = self._inverter_spec()
        metrics = {"vm": 0.9, "tpd": 100e-9}
        passed, err = spec.score(metrics)
        assert passed
        assert err < 0.01

    def test_vm_off_fails(self):
        spec = self._inverter_spec()
        metrics = {"vm": 0.5, "tpd": 100e-9}
        passed, err = spec.score(metrics)
        assert not passed
        assert err > 0

    def test_tpd_over_limit_fails(self):
        spec = self._inverter_spec()
        metrics = {"vm": 0.9, "tpd": 300e-9}
        passed, err = spec.score(metrics)
        assert not passed

    def test_both_fail_higher_error(self):
        spec = self._inverter_spec()
        m_both = {"vm": 0.5, "tpd": 400e-9}
        m_one  = {"vm": 0.9, "tpd": 400e-9}
        _, e_both = spec.score(m_both)
        _, e_one  = spec.score(m_one)
        assert e_both > e_one

    def test_derived_metric_computed(self):
        """tpd_sum derived from tphl_sum + tplh_sum via formula."""
        spec = _make_spec([
            MetricTarget("tpd_sum", "derived", "min", 200e-9,
                         formula="(abs(tphl_sum)+abs(tplh_sum))/2"),
        ])
        metrics = {"tphl_sum": 100e-9, "tplh_sum": 120e-9}
        passed, err = spec.score(metrics)
        # Expected tpd_sum = 110ns which is < 200ns → should pass
        assert passed

    def test_full_adder_all_pass(self):
        spec = _make_spec([
            MetricTarget("tpd_sum",  "derived", "min", 200e-9),
            MetricTarget("tpd_cout", "derived", "min", 150e-9),
        ])
        metrics = {"tpd_sum": 130e-9, "tpd_cout": 100e-9}
        passed, err = spec.score(metrics)
        assert passed


# ---------------------------------------------------------------------------
# YAML round-trip
# ---------------------------------------------------------------------------
class TestYAMLRoundTrip:
    def test_to_and_from_yaml(self, tmp_path):
        spec = _make_spec([
            MetricTarget("tpd", "tran", "min", 200e-9, weight=2.0),
        ])
        spec.circuit_name = "roundtrip_test"
        spec.template_dc   = Path("dc.cir")
        spec.template_tran = Path("tran.cir")

        yaml_path = tmp_path / "spec.yaml"
        spec.to_yaml(yaml_path)
        loaded = DynamicSpec.from_yaml(yaml_path)

        assert loaded.circuit_name == spec.circuit_name
        assert len(loaded.metric_targets) == 1
        assert loaded.metric_targets[0].name == "tpd"
        assert loaded.metric_targets[0].weight == pytest.approx(2.0)
