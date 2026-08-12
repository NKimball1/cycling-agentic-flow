"""Phase 1 entry point: manually trigger a ride analysis.

Usage:
    python analyze.py --fit data/ride.fit

Prints the analysis as Markdown and saves it to output/analysis_<date>.md.
"""

from __future__ import annotations

import argparse
from datetime import datetime

import config
import notify
from fit_parser import get_activity_metrics
from coach import run_analysis


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze a cycling ride against your training plan.")
    parser.add_argument(
        "--fit",
        default=str(config.DATA_DIR / "ride.fit"),
        help="Path to the .fit file to analyze (default: data/ride.fit).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Don't print the note about what data was gathered.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the full analysis but do NOT write the activity to history.",
    )
    args = parser.parse_args()

    print(f"Analyzing: {args.fit}")
    print(f"Model: {config.CLAUDE_MODEL}  |  FTP: {config.FTP_WATTS} W", end="")
    print("  |  DRY RUN (history not written)" if args.dry_run else "")
    print()

    activity = get_activity_metrics(
        args.fit,
        ftp=config.FTP_WATTS,
        resting_hr=config.RESTING_HR,
        threshold_hr=config.THRESHOLD_HR,
    )
    analysis = run_analysis(activity, verbose=not args.quiet, dry_run=args.dry_run)

    # Print the final analysis.
    print("\n" + "=" * 70 + "\n")
    print(analysis)

    # Save it alongside the date so you build up a history of analyses.
    config.OUTPUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_path = config.OUTPUT_DIR / f"analysis_{stamp}.md"
    out_path.write_text(analysis, encoding="utf-8")
    print("\n" + "=" * 70)
    print(f"Saved to {out_path}")

    # Deliver it (Phase 3) unless this was a dry run.
    if not args.dry_run:
        subject = (
            f"\U0001f6b4 {(activity.get('sport') or 'activity').title()} analysis — "
            f"{(activity.get('date') or '')[:10]}"
        )
        try:
            notify.notify(subject, analysis, verbose=not args.quiet)
        except Exception as exc:
            print(f"(delivery failed: {exc})")


if __name__ == "__main__":
    main()
