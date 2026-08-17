"""Unit tests for metrics.py — the shared unit/load math both data sources rely on.

These are pure functions with no I/O, so they're fast, deterministic, and a good
first safety net: if a refactor ever changes how TSS or pace is computed, these
fail loudly instead of silently shipping wrong numbers into a coaching analysis.
"""

import pytest

import metrics


def test_unit_conversions():
    assert metrics.m_to_mi(metrics.METERS_PER_MILE) == 1.0
    assert metrics.m_to_ft(100) == 328
    assert metrics.mps_to_mph(10) == 22.4
    assert metrics.c_to_f(0) == 32
    assert metrics.c_to_f(100) == 212


def test_conversions_pass_through_none():
    # Missing input must never crash — it flows through as None.
    assert metrics.m_to_mi(None) is None
    assert metrics.m_to_ft(None) is None
    assert metrics.mps_to_mph(None) is None
    assert metrics.c_to_f(None) is None
    assert metrics.pace_min_per_mi(None) is None
    assert metrics.pace_str(None) is None


@pytest.mark.parametrize("mps", [1.5, 2.0, 2.2699, 3.0, 4.4704, 5.5])
def test_pace_str_never_mangles_seconds(mps):
    # Guards the real "11:81" bug: the seconds field must always be a valid 00-59.
    minutes, seconds = metrics.pace_str(mps).split(":")
    assert len(seconds) == 2
    assert 0 <= int(seconds) < 60


def test_pace_str_known_value():
    # ~4.4704 m/s == 6:00 min/mile (10 mph).
    assert metrics.pace_str(metrics.METERS_PER_MILE / 360) == "6:00"


def test_pace_zero_or_negative_is_none():
    assert metrics.pace_str(0) is None
    assert metrics.pace_min_per_mi(-1) is None


def test_power_tss_one_hour_at_ftp_is_100():
    # An hour at exactly FTP is 100 TSS by definition.
    assert metrics.power_tss(3600, 250, 250) == 100.0


def test_power_tss_requires_all_inputs():
    assert metrics.power_tss(0, 250, 250) is None
    assert metrics.power_tss(3600, None, 250) is None


def test_hr_tss_one_hour_at_threshold_is_100():
    assert metrics.hr_tss(3600, 163, 39, 163) == 100.0


def test_hr_tss_sub_resting_clamps_to_zero():
    # A sub-resting average shouldn't produce negative load.
    assert metrics.hr_tss(3600, 30, 39, 163) == 0.0


def test_hr_tss_bad_anchors_return_none():
    assert metrics.hr_tss(3600, 150, 163, 163) is None  # threshold <= resting
    assert metrics.hr_tss(3600, None, 39, 163) is None


def test_estimated_tss():
    assert metrics.estimated_tss(3600, 1.0) == 100.0
    assert metrics.estimated_tss(0) is None
    assert metrics.estimated_tss(3600) == pytest.approx(42.25, abs=0.1)


def test_normalized_power_steady_effort():
    # Perfectly steady power normalizes to that same power.
    assert metrics.normalized_power([200] * 60) == 200


def test_normalized_power_needs_30_samples():
    assert metrics.normalized_power([200] * 29) is None
