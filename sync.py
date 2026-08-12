"""Phase 2 automation: pull new Strava activities, analyze, and log them.

Each run:
  1. lists your recent Strava activities (1 read),
  2. skips any already handled (tracked in .synced_ids.json),
  3. for each NEW one: fetch detail (+1 read) -> map -> analyze -> log ->
     save the write-up to output/.

Idempotent via the synced-ids file, so polling often is safe and cheap.

Trigger it however you like:
  python sync.py                 # analyze any new activities
  python sync.py --dry-run       # analyze new ones but DON'T log/save/mark
  python sync.py --mark-synced   # baseline: mark current activities as handled,
                                 #   WITHOUT analyzing (no Claude calls). Run this
                                 #   ONCE first so it won't re-analyze the history
                                 #   you already curated by hand.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import config
import strava
import notify
from coach import run_analysis


def _load_synced_ids() -> set[int]:
    path = Path(config.SYNCED_IDS_PATH)
    if not path.exists():
        return set()
    return set(json.loads(path.read_text(encoding="utf-8")))


def _save_synced_ids(ids: set[int]) -> None:
    Path(config.SYNCED_IDS_PATH).write_text(
        json.dumps(sorted(ids), indent=2), encoding="utf-8"
    )


def _save_analysis(activity: dict, text: str) -> Path:
    config.OUTPUT_DIR.mkdir(exist_ok=True)
    date = (activity.get("date") or "")[:10] or datetime.now().strftime("%Y-%m-%d")
    sport = activity.get("sport") or "activity"
    aid = activity.get("strava_activity_id")
    path = config.OUTPUT_DIR / f"analysis_{date}_{sport}_{aid}.md"
    path.write_text(text, encoding="utf-8")
    return path


def _process(a: dict, args, synced: set[int]) -> None:
    """Analyze one activity (given its Strava summary dict) and deliver it."""
    aid = a["id"]
    print("=" * 70)
    print(f"{a.get('start_date_local', '')[:10]}  {a.get('sport_type', '')}  {a.get('name', '')}  (id {aid})")
    print("=" * 70)

    activity = strava.get_activity_metrics(
        aid,
        ftp=config.FTP_WATTS,
        resting_hr=config.RESTING_HR,
        threshold_hr=config.THRESHOLD_HR,
    )
    text = run_analysis(activity, verbose=not args.quiet, dry_run=args.dry_run)
    print("\n" + text + "\n")

    if args.dry_run:
        print("[dry-run] not saved, not logged, not marked synced, not sent.\n")
        return

    out = _save_analysis(activity, text)
    synced.add(aid)
    _save_synced_ids(synced)  # persist after each one, so a crash mid-run doesn't repeat work
    print(f"Saved: {out}")

    # Deliver it (Phase 3). Never let a delivery hiccup lose the analysis.
    subject = (
        f"\U0001f6b4 {a.get('sport_type', 'Activity')} analysis — "
        f"{a.get('start_date_local', '')[:10]} — {a.get('name', '')}"
    )
    try:
        notify.notify(subject, text, verbose=not args.quiet)
    except Exception as exc:
        print(f"  (delivery failed: {exc})")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull new Strava activities and analyze them.")
    parser.add_argument("--limit", type=int, default=10, help="How many recent activities to scan (default 10).")
    parser.add_argument("--dry-run", action="store_true", help="Analyze new activities but don't log, save, or mark them synced.")
    parser.add_argument("--mark-synced", action="store_true", help="Mark current activities as already handled WITHOUT analyzing (baseline).")
    parser.add_argument("--quiet", action="store_true", help="Less chatter during analysis.")
    parser.add_argument("--activity", type=int, metavar="ID", help="Analyze one specific Strava activity id, ignoring the synced list (use with --dry-run to iterate on the prompt).")
    args = parser.parse_args()

    synced = _load_synced_ids()

    # --- Specific-activity mode: re-analyze one activity by id, bypassing the
    #     synced list. Ideal with --dry-run for iterating on the coach prompt. ---
    if args.activity:
        detail = strava.get_activity(args.activity)
        _process(
            {
                "id": detail.get("id"),
                "name": detail.get("name", ""),
                "sport_type": detail.get("sport_type", ""),
                "start_date_local": detail.get("start_date_local", ""),
            },
            args,
            synced,
        )
        return

    activities = strava.list_recent_activities(per_page=args.limit)

    # --- Baseline mode: mark everything current as handled, analyze nothing. ---
    if args.mark_synced:
        before = len(synced)
        synced.update(a["id"] for a in activities)
        _save_synced_ids(synced)
        print(
            f"Baseline set: {len(synced) - before} activities marked as already "
            f"handled ({len(synced)} total). Future syncs will only process new ones."
        )
        return

    # --- Find genuinely new activities, analyze oldest-first. ---
    new = [a for a in activities if a["id"] not in synced]
    if not new:
        print("No new activities. Up to date.")
        return
    new.sort(key=lambda a: a.get("start_date_local", ""))  # oldest first, so history builds in order

    print(f"Found {len(new)} new activit{'y' if len(new) == 1 else 'ies'} to analyze.\n")

    for a in new:
        _process(a, args, synced)

    print("Done.")


if __name__ == "__main__":
    main()
