"""Load the training plan and activity history, and append new activities.

For Phase 1 these are simple JSON files under data/. In Phase 2 the activity
history could come straight from Strava; only these functions would change, not
the analysis step that consumes them.

`append_activity()` is the write side of the `log_activity` tool: after Claude
analyzes an activity, it hands us a structured entry and we persist it here so
future analyses have it as context. The model produces the record; this code
owns the actual file write — that's the safety boundary.

Run standalone to test:
    python plan_loader.py
"""

from __future__ import annotations

import json

import config
import metrics


# --- History schema normalization -----------------------------------------
# Hand-curated history accumulated schema drift (some entries use `tss` vs
# `load_tss`, `notes` vs `note`, nest fields under `key_metrics`, or omit
# `sport`). normalize_entry() folds any entry into one flat, consistent shape so
# every consumer — the coach's context, the load model — reads the same fields.
# It runs on every write (append_activity), so new entries can't re-introduce
# drift, and the one-time normalize_history.py uses it to clean the backlog.

def _infer_sport(entry: dict) -> str | None:
    t = (entry.get("type") or "").lower()
    if "run" in t:
        return "running"
    if "walk" in t:
        return "walking"
    if "hike" in t:
        return "hiking"
    if "swim" in t:
        return "swimming"
    # Rides are labeled by workout type (Endurance, Threshold, Long Endurance...).
    return "cycling"


def _infer_load_source(entry: dict) -> str:
    if entry.get("normalized_power_w") or entry.get("avg_power_w") or entry.get("interval_1_w"):
        return "power"
    if entry.get("avg_hr_bpm"):
        return "hr"
    return "estimated"


def normalize_entry(entry: dict) -> dict:
    """Return a flat, consistently-keyed copy of a history entry.

    - flattens `key_metrics` up to the top level (existing top-level keys win)
    - unifies `notes` -> `note`, `tss` -> `load_tss`, `duration_min` ->
      `moving_duration_min`
    - guarantees `sport` (inferred from `type`) and `load_source`
    - fills `load_tss` from an HR-derived estimate when nothing else is present
    """
    e = dict(entry)

    km = e.pop("key_metrics", None)
    if isinstance(km, dict):
        for k, v in km.items():
            e.setdefault(k, v)  # don't overwrite an existing top-level value

    if "note" not in e and "notes" in e:
        e["note"] = e["notes"]
    e.pop("notes", None)

    if e.get("load_tss") is None and e.get("tss") is not None:
        e["load_tss"] = e["tss"]
    e.pop("tss", None)

    if "moving_duration_min" not in e and "duration_min" in e:
        e["moving_duration_min"] = e["duration_min"]
    e.pop("duration_min", None)

    if not e.get("sport"):
        e["sport"] = _infer_sport(e)

    # Derive a load if the entry never carried one but has HR + duration.
    if e.get("load_tss") is None:
        dur = e.get("moving_duration_min")
        hr = e.get("avg_hr_bpm")
        if dur and hr:
            e["load_tss"] = metrics.hr_tss(dur * 60, hr, config.RESTING_HR, config.THRESHOLD_HR)

    if e.get("load_tss") is not None and not e.get("load_source"):
        e["load_source"] = _infer_load_source(e)

    return e


def get_training_plan() -> dict:
    """Return the current training plan (goals, philosophy, weekly structure)."""
    with open(config.TRAINING_PLAN_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_recent_activities() -> list:
    """Return recent activities (all sports, most recent first) for context."""
    with open(config.RECENT_ACTIVITIES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def append_activity(entry: dict) -> dict:
    """Add a newly-analyzed activity to the history file.

    Keeps the list sorted most-recent-first. If an entry with the same date and
    name already exists it is replaced rather than duplicated, so re-running an
    analysis doesn't pile up copies.

    Returns a small status dict for the tool result.
    """
    activities = get_recent_activities()

    entry = normalize_entry(entry)  # keep the persisted shape consistent
    key = (entry.get("date"), entry.get("name"))
    activities = [a for a in activities if (a.get("date"), a.get("name")) != key]
    activities.append(entry)

    # Sort newest first; entries without a date sink to the bottom.
    activities.sort(key=lambda a: a.get("date") or "", reverse=True)

    with open(config.RECENT_ACTIVITIES_PATH, "w", encoding="utf-8") as f:
        json.dump(activities, f, indent=2)
        f.write("\n")

    return {"status": "logged", "activity": entry.get("name"), "total_activities": len(activities)}


if __name__ == "__main__":
    print("=== Training plan ===")
    print(json.dumps(get_training_plan(), indent=2))
    print("\n=== Recent activities ===")
    print(json.dumps(get_recent_activities(), indent=2))
