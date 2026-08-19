"""Unit tests for training_load.py — the rolling Fitness/Fatigue/Form model.

Covers the load-selection fallback chain, the daily-TSS series, the form labels,
and the compute_load shape/invariants. These guard the numbers the coach reasons
about (CTL/ATL/TSB), so a refactor can't silently change an athlete's readiness
read.
"""

from datetime import date

import training_load as tl


def test_form_label_bands():
    assert tl.form_label(20) == "very fresh / tapered"
    assert tl.form_label(10) == "fresh"
    assert tl.form_label(0) == "neutral / balanced"
    assert tl.form_label(-20) == "fatigued (productive training stress)"
    assert tl.form_label(-40) == "very fatigued (high strain — monitor)"


def test_activity_load_prefers_explicit_value():
    assert tl.activity_load({"load_tss": 100}) == 100.0
    assert tl.activity_load({"tss": 80}) == 80.0             # older field name still works
    assert tl.activity_load({"load_tss": 50, "tss": 999}) == 50.0  # load_tss wins over tss


def test_activity_load_derives_from_hr_when_no_explicit_load():
    # No load_tss/tss, but duration + HR are enough for an HR-based estimate.
    load = tl.activity_load({"moving_duration_min": 60, "avg_hr_bpm": 163})
    assert load is not None and load > 0


def test_activity_load_none_when_nothing_usable():
    assert tl.activity_load({"sport": "walking"}) is None  # no load, no HR/duration


def test_daily_tss_sums_per_day_and_skips_unusable():
    acts = [
        {"date": "2026-01-01", "load_tss": 50},
        {"date": "2026-01-01", "load_tss": 30},  # same day -> summed
        {"date": "2026-01-02", "load_tss": 40},
        {"date": None, "load_tss": 99},           # undated -> skipped
        {"date": "2026-01-03", "sport": "walk"},   # no usable load -> skipped
    ]
    series = tl.daily_tss(acts)
    assert series[date(2026, 1, 1)] == 80.0
    assert series[date(2026, 1, 2)] == 40.0
    assert date(2026, 1, 3) not in series


def test_compute_load_bad_date_returns_none():
    assert tl.compute_load([{"date": "2026-01-01", "load_tss": 50}], "not-a-date") is None


def test_compute_load_none_when_no_history_and_no_activity():
    assert tl.compute_load([], "2026-01-06") is None


def test_compute_load_shape_and_invariants():
    prior = [
        {"date": "2026-01-01", "load_tss": 60},
        {"date": "2026-01-03", "load_tss": 90},
        {"date": "2026-01-05", "load_tss": 60},
    ]
    state = tl.compute_load(prior, "2026-01-06", activity_tss=100)

    for key in (
        "fitness_ctl", "fatigue_atl", "form_tsb_pre", "form_tsb_post",
        "form_label", "days_of_history", "warming_up", "seed_value",
    ):
        assert key in state

    # A short window can't have matured a 42-day CTL yet.
    assert state["warming_up"] is True
    # The label must be derived from the same helper on the pre-activity form.
    assert state["form_label"] == tl.form_label(state["form_tsb_pre"])
    # Folding in today's load raises fatigue faster than fitness, so form drops.
    assert state["form_tsb_post"] <= state["form_tsb_pre"]
