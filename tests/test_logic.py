import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spe_logic import freq_to_band, curve_interpolate, curve_update, choose_ff_drive, last_drive_key, curve_key, amp_band_from_freq


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


def test_ff_remembers_per_target():
    curve = {40: 100.0, 60: 300.0}
    mem = {last_drive_key("40", "DIGU", "LOW", 1): {300: 55, 200: 32}}
    assert choose_ff_drive(mem, curve, "40", "DIGU", 300, "LOW", 1) == 55
    assert choose_ff_drive(mem, curve, "40", "DIGU", 200, "LOW", 1) == 32
    assert choose_ff_drive(mem, curve, "40", "DIGU", 300, "LOW", 1) == 55   # tillbaka till 300


def test_ff_falls_back_to_curve():
    curve = {40: 100.0, 60: 300.0}
    mem = {last_drive_key("20", "CW", "LOW", 1): {300: 55}}
    assert choose_ff_drive(mem, curve, "20", "CW", 200, "LOW", 1) == 50      # annat börvärde
    assert choose_ff_drive(mem, curve, "20", "DIGU", 300, "LOW", 1) == 60    # annat mode
    assert choose_ff_drive({}, None, "20", "CW", 300, "LOW", 1) is None


def test_high_and_low_do_not_mix():
    mem = {last_drive_key("40", "CW", "LOW", 1): {300: 55}}
    assert choose_ff_drive(mem, None, "40", "CW", 300, "LOW", 1) == 55
    assert choose_ff_drive(mem, None, "40", "CW", 300, "HIGH", 1) is None
    assert curve_key("40", "LOW", 1) != curve_key("40", "HIGH", 1)


def test_amp_band_from_freq_matches_curve_keys():
    assert amp_band_from_freq(7_040_000) == "40"
    assert amp_band_from_freq(10_136_000) == "30"
    assert amp_band_from_freq(14_074_000) == "20"
    assert amp_band_from_freq(50_313_000) == "6"
    assert amp_band_from_freq(14_099_500) is None   # glapp
    assert amp_band_from_freq(None) is None
    # alla nycklar i settings-kurvan (10 12 15 17 20 30 40 6 80) ska kunna genereras
    got = {amp_band_from_freq(f) for f in (28_300_000, 24_930_000, 21_074_000, 18_100_000,
           14_074_000, 10_136_000, 7_074_000, 50_313_000, 3_573_000)}
    assert got == {"10", "12", "15", "17", "20", "30", "40", "6", "80"}


def test_rx1_and_rx2_do_not_mix():
    mem = {last_drive_key("40", "DIGU", "LOW", 1): {300: 55}}      # RX2 (trx 1)
    assert choose_ff_drive(mem, None, "40", "DIGU", 300, "LOW", 1) == 55
    assert choose_ff_drive(mem, None, "40", "DIGU", 300, "LOW", 0) is None   # RX1 har inget minne
    assert curve_key("40", "LOW", 0) != curve_key("40", "LOW", 1)
    assert last_drive_key("40", "DIGU", "LOW", 0).endswith("|RX1")
