import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spe_logic import freq_to_band, curve_interpolate, curve_update


def test_freq_to_band_edges_and_gaps():
    assert freq_to_band(14_200_000) == "20h"
    assert freq_to_band(14_000_000) == "20l"
    assert freq_to_band(14_099_000) == "20l"
    assert freq_to_band(14_099_500) is None      # glapp mellan l och h
    assert freq_to_band(50_131_000) == "6h"
    assert freq_to_band(1_000_000) is None


def test_interpolate_needs_two_points():
    assert curve_interpolate(None, 100) is None
    assert curve_interpolate({}, 100) is None
    assert curve_interpolate({40: 100.0}, 100) is None


def test_interpolate_between_points():
    pts = {40: 100.0, 60: 300.0}
    assert curve_interpolate(pts, 200.0) == 50
    assert curve_interpolate(pts, 100.0) == 40
    assert curve_interpolate(pts, 300.0) == 60


def test_interpolate_equal_power_returns_lower_drive():
    assert curve_interpolate({40: 100.0, 50: 100.0}, 100.0) == 40


def test_interpolate_extrapolates_two_steps_and_clamps():
    pts = {40: 100.0, 60: 300.0}
    assert curve_interpolate(pts, 50.0) == 38
    assert curve_interpolate(pts, 400.0) == 62
    assert curve_interpolate({0: 100.0, 99: 300.0}, 400.0) == 100
    assert curve_interpolate({1: 100.0, 20: 300.0}, 10.0) == 0


def test_curve_update_new_point_and_slow_merge():
    curve = {}
    curve_update(curve, 50, 200.04)
    assert curve == {50: 200.0}
    curve_update(curve, 50, 300.0)
    assert curve == {50: 230.0}     # 0.7*200 + 0.3*300
