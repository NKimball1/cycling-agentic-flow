"""One-time cleanup: normalize the activity-history file into one flat schema.

The hand-curated history accumulated schema drift (mixed `tss`/`load_tss`,
`notes`/`note`, some fields nested under `key_metrics`, missing `sport`). This
rewrites every entry through plan_loader.normalize_entry() so all consumers —
the coach's context and the training-load model — read consistent fields.

Safe by construction: it writes a timestamped .backup first, prints a summary of
what changed per entry, and (unless --apply is passed) does a DRY RUN that
touches nothing.

    python normalize_history.py            # preview only
    python normalize_history.py --apply    # back up + rewrite the file
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

import config
import storage
from plan_loader import normalize_entry


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize recent_activities.json to a flat, consistent schema.")
    parser.add_argument("--apply", action="store_true", help="Write the changes (default is a dry-run preview).")
    args = parser.parse_args()

    path = config.RECENT_ACTIVITIES_PATH
    activities = _load(path)
    normalized = [normalize_entry(a) for a in activities]

    print(f"{len(activities)} entries in {path}\n")
    print(f"{'date':<12}{'sport':<10}{'load_tss':>9}{'source':>10}   changed keys")
    print("-" * 70)
    load_before = sum(1 for a in activities if a.get("load_tss") is not None)
    load_after = sum(1 for a in normalized if a.get("load_tss") is not None)
    for before, after in zip(activities, normalized):
        added = sorted(set(after) - set(before))
        removed = sorted(set(before) - set(after))
        changes = ", ".join(
            [f"+{k}" for k in added] + [f"-{k}" for k in removed]
        ) or "(none)"
        lt = after.get("load_tss")
        print(
            f"{(after.get('date') or '')[:10]:<12}{(after.get('sport') or '?'):<10}"
            f"{(round(lt, 1) if lt is not None else '-'):>9}{(after.get('load_source') or '-'):>10}   {changes}"
        )

    print(f"\nEntries with load_tss: {load_before} -> {load_after} of {len(activities)}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to back up and save.")
        return

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.stem}.backup-{stamp}.json")
    with open(backup, "w", encoding="utf-8") as f:
        json.dump(activities, f, indent=2)
        f.write("\n")
    storage.write_json_atomic(path, normalized)
    print(f"\nBacked up original -> {backup}")
    print(f"Wrote normalized   -> {path}")


if __name__ == "__main__":
    main()
