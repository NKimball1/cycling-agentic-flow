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
