"""Unit tests for plan_loader.normalize_entry — the history schema unifier.

This function is what stops schema drift from creeping back into the activity
history (mixed tss/load_tss, notes/note, nested key_metrics, missing sport). It
runs on every write, so if it regresses, bad data silently re-enters — these
tests pin its behavior.
"""

from plan_loader import normalize_entry


def test_flattens_key_metrics_to_top_level():
    entry = {
        "date": "2026-01-01",
        "type": "Endurance",
        "key_metrics": {"load_tss": 100, "avg_power_w": 150},
    }
    out = normalize_entry(entry)
    assert "key_metrics" not in out
    assert out["load_tss"] == 100
    assert out["avg_power_w"] == 150


def test_key_metrics_does_not_overwrite_top_level():
    # An existing top-level value wins over a nested one.
    out = normalize_entry({"type": "Endurance", "load_tss": 200, "key_metrics": {"load_tss": 100}})
    assert out["load_tss"] == 200


def test_renames_legacy_fields():
    out = normalize_entry({"type": "Threshold", "tss": 108, "notes": "hot day", "duration_min": 90})
    assert out["load_tss"] == 108 and "tss" not in out
    assert out["note"] == "hot day" and "notes" not in out
    assert out["moving_duration_min"] == 90 and "duration_min" not in out


def test_infers_sport_from_type():
    assert normalize_entry({"type": "Run - Easy"})["sport"] == "running"
    assert normalize_entry({"type": "Walk"})["sport"] == "walking"
    assert normalize_entry({"type": "Hike"})["sport"] == "hiking"
    assert normalize_entry({"type": "Endurance"})["sport"] == "cycling"
    assert normalize_entry({"type": "Threshold"})["sport"] == "cycling"


def test_explicit_sport_is_preserved():
    # Inference would say "running", but an explicit value must not be overwritten.
    assert normalize_entry({"type": "Run", "sport": "cycling"})["sport"] == "cycling"


def test_derives_load_from_hr_when_missing():
    out = normalize_entry({"type": "Run - Long", "moving_duration_min": 60, "avg_hr_bpm": 163})
    assert out["load_tss"] is not None and out["load_tss"] > 0
    assert out["load_source"] == "hr"


def test_load_source_is_power_when_power_present():
    out = normalize_entry({"type": "Endurance", "load_tss": 100, "normalized_power_w": 200})
    assert out["load_source"] == "power"


def test_no_load_no_source_when_nothing_measurable():
    out = normalize_entry({"type": "Walk"})  # no load, no HR/duration
    assert out.get("load_tss") is None
    assert "load_source" not in out


def test_does_not_mutate_input():
    entry = {"type": "Endurance", "tss": 90}
    normalize_entry(entry)
    assert entry == {"type": "Endurance", "tss": 90}  # original untouched
