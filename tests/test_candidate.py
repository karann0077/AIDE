"""
tests/test_candidate.py — Unit tests for Candidate.to_spice_params()
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from engines.base import Candidate


class TestToSpiceParams:
    def test_micrometers(self):
        c = Candidate(values={"mn1_w": 2e-6})
        params = c.to_spice_params()
        assert "u" in params["mn1_w"]
        val = float(params["mn1_w"].replace("u", "")) * 1e-6
        assert abs(val - 2e-6) < 1e-15

    def test_nanometers(self):
        c = Candidate(values={"mn1_l": 180e-9})
        params = c.to_spice_params()
        assert "n" in params["mn1_l"]

    def test_millimeters(self):
        c = Candidate(values={"big": 5e-3})
        params = c.to_spice_params()
        assert "m" in params["big"]

    def test_all_four_variables(self):
        c = Candidate(values={
            "mn1_w": 2e-6, "mn1_l": 180e-9,
            "mp1_w": 4e-6, "mp1_l": 180e-9,
        })
        params = c.to_spice_params()
        assert len(params) == 4
        assert all("u" in params["mn1_w"] for _ in [1])
