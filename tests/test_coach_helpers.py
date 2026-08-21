"""Unit tests for the fact-builder helpers in coach.py.

These are the pure functions that inject *authoritative, code-computed* context
into the prompt — weekdays, inter-activity gaps, the weekday calendar, weather.
They exist specifically because the model is unreliable at date arithmetic, so
regressions here reintroduce exactly the bugs they were built to kill (wrong
weekdays, "N days ago" errors). All pure, no I/O.
"""

import coach


# --- _weekday -------------------------------------------------------------
def test_weekday_known_dates():
    assert coach._weekday("2026-08-18") == "Tuesday"
    assert coach._weekday("2026-08-15") == "Saturday"
    # A time suffix is tolerated (only the date part is used).
    assert coach._weekday("2026-08-18T09:00:00") == "Tuesday"


def test_weekday_bad_input_is_none():
    assert coach._weekday(None) is None
    assert coach._weekday("not-a-date") is None
    assert coach._weekday("2026-13-99") is None


# --- _days_before ---------------------------------------------------------
def test_days_before_signs():
    assert coach._days_before("2026-08-18", "2026-08-15") == 3    # other is 3 days before
    assert coach._days_before("2026-08-18", "2026-08-18") == 0    # same day
    assert coach._days_before("2026-08-18", "2026-08-20") == -2   # other is after


def test_days_before_bad_input_is_none():
    assert coach._days_before(None, "2026-08-18") is None
    assert coach._days_before("2026-08-18", "garbage") is None


# --- _timing_facts --------------------------------------------------------
def test_timing_facts_gap_and_weekday():
    out = coach._timing_facts("2026-08-18", [{"date": "2026-08-15", "name": "Ride A"}])
    assert "Ride A" in out
    assert "Saturday" in out
    assert "3 days before this activity" in out


def test_timing_facts_singular_and_same_day():
    assert "1 day before this activity" in coach._timing_facts(
        "2026-08-18", [{"date": "2026-08-17", "name": "B"}]
    )
    assert "same day as this activity" in coach._timing_facts(
        "2026-08-18", [{"date": "2026-08-18", "name": "C"}]
    )


def test_timing_facts_name_falls_back_to_type():
    out = coach._timing_facts("2026-08-18", [{"date": "2026-08-15", "type": "Endurance"}])
    assert "Endurance" in out


def test_timing_facts_empty_when_nothing_usable():
    assert coach._timing_facts("2026-08-18", []) == ""
    assert coach._timing_facts("2026-08-18", [{"date": "bad", "name": "X"}]) == ""


# --- _calendar_facts ------------------------------------------------------
def test_calendar_facts_maps_date_to_weekday():
    out = coach._calendar_facts("2026-08-18")
    assert "Weekday calendar" in out
    assert "Tue 08-18" in out   # the exact mapping that a model kept getting wrong


def test_calendar_facts_empty_on_bad_date():
    assert coach._calendar_facts("nope") == ""


# --- _weather_facts -------------------------------------------------------
def test_weather_facts_renders_when_present():
    out = coach._weather_facts({"weather": {"temp_f": 72, "conditions": "Overcast"}})
    assert "Weather at the start" in out
    assert "72" in out
    assert "Overcast" in out


def test_weather_facts_empty_when_absent():
    assert coach._weather_facts({}) == ""
