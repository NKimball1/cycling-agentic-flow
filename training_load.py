"""Rolling training-load state — Fitness (CTL), Fatigue (ATL), Form (TSB).

This is the "Performance Management Chart" model. From a stream of daily training
stress (TSS), three exponentially-weighted averages tell you where the athlete
stands:

  CTL (Chronic Training Load, ~42-day)  -> "Fitness"  (slow-moving)
  ATL (Acute Training Load,   ~7-day)   -> "Fatigue"  (fast-moving)
  TSB (Training Stress Balance)         -> "Form" = CTL - ATL

Positive TSB = fresh/tapered; mildly negative = productive training; deeply
negative = high strain, worth flagging.

Design: we DERIVE this from the activity history every time rather than storing a
running value. History (recent_activities.json) is already the source of truth
and every entry carries `load_tss` + a date, so recomputing is idempotent — a
re-analysis or a backfill can't corrupt a stored counter. Same principle as the
date facts and weather: compute deterministic facts in code, hand them to the
model.

Cold-start caveat: CTL's 42-day memory needs ~6 weeks of history to be accurate.
With a shorter window, starting from zero understates fitness and makes form look
falsely fresh. We seed CTL/ATL with the window's average daily load as a best
prior and flag `warming_up` so nothing over-trusts an immature CTL.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import config
import metrics

CTL_DAYS = 42  # fitness time constant (days)
ATL_DAYS = 7   # fatigue time constant (days)


def activity_load(a: dict):
    """Best available load for one activity, tolerating history schema drift.

    Prefers an explicit load value (`load_tss`, or the older `tss` field); if
    neither exists, derives an HR-based load from duration + avg HR (the same
    fallback the live data sources use). Returns None when nothing is usable —
    e.g. a walk with no HR — so it simply contributes 0 to the series.
    """
    for key in ("load_tss", "tss"):
        if a.get(key) is not None:
            return float(a[key])
    duration_min = a.get("moving_duration_min") or a.get("duration_min")
    avg_hr = a.get("avg_hr_bpm")
    if duration_min and avg_hr:
        return metrics.hr_tss(
            duration_min * 60, avg_hr, config.RESTING_HR, config.THRESHOLD_HR
        )
    return None


def _parse_date(value):
    """Date from a 'YYYY-MM-DD...' string, or None."""
    try:
        return datetime.strptime((value or "")[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def daily_tss(activities: list) -> dict:
    """Sum `load_tss` per calendar day across activities. Entries without a
    parseable date or without load_tss are skipped (they contribute 0)."""
    out: dict = {}
    for a in activities:
        d = _parse_date(a.get("date"))
        tss = activity_load(a)
        if d is None or tss is None:
            continue
        out[d] = out.get(d, 0.0) + float(tss)
    return out


def form_label(tsb: float) -> str:
    """Plain-language read of a TSB (form) value."""
    if tsb > 15:
        return "very fresh / tapered"
    if tsb > 5:
        return "fresh"
    if tsb >= -10:
        return "neutral / balanced"
    if tsb >= -30:
        return "fatigued (productive training stress)"
    return "very fatigued (high strain — monitor)"


def compute_load(
    prior_activities: list,
    activity_date,
    activity_tss=None,
    ctl_days: int = CTL_DAYS,
    atl_days: int = ATL_DAYS,
) -> dict | None:
    """Roll CTL/ATL day-by-day from history up to `activity_date`.

    `prior_activities` is the history NOT including the activity being analyzed
    (we log that only after analysis). `activity_tss` is today's load, folded in
    AFTER computing the pre-activity form — so we can report both the form the
    athlete carried INTO the session and the state it leaves them in.

    Returns a dict of the load state, or None if there's nothing to compute from.
    """
    as_of = _parse_date(activity_date)
    if as_of is None:
        return None

    series = {d: v for d, v in daily_tss(prior_activities).items() if d <= as_of}
    if not series and not activity_tss:
        return None

    start = min(series) if series else as_of
    days_of_history = (as_of - start).days + 1

    # Cold-start seed: best estimate of prior fitness is the training density we
    # can actually see. Seeding both CTL and ATL to the window's average daily
    # load avoids a falsely-fresh reading when history is short.
    span_days = ((max(series) - start).days + 1) if series else 1
    seed = (sum(series.values()) / span_days) if span_days > 0 else 0.0
    warming_up = days_of_history < ctl_days  # CTL not fully matured yet

    # Exponential (impulse-response) smoothing constants.
    alpha_ctl = 1 - math.exp(-1 / ctl_days)
    alpha_atl = 1 - math.exp(-1 / atl_days)

    ctl = atl = seed
    ctl_week_ago = None
    cur = start
    # Advance to the day BEFORE the activity: this leaves ctl/atl at the
    # end-of-yesterday state, which is the form the athlete took into the session.
    while cur < as_of:
        tss = series.get(cur, 0.0)
        ctl += (tss - ctl) * alpha_ctl
        atl += (tss - atl) * alpha_atl
        if cur == as_of - timedelta(days=7):
            ctl_week_ago = ctl
        cur += timedelta(days=1)

    tsb_pre = ctl - atl  # form going INTO this activity

    # Fold in today's load (any history on this date + the analyzed activity).
    tss_today = series.get(as_of, 0.0) + (float(activity_tss) if activity_tss else 0.0)
    ctl += (tss_today - ctl) * alpha_ctl
    atl += (tss_today - atl) * alpha_atl
    tsb_post = ctl - atl  # form leaving this activity (for tomorrow)

    ramp = round(ctl - ctl_week_ago, 1) if ctl_week_ago is not None else None

    return {
        "fitness_ctl": round(ctl, 1),
        "fatigue_atl": round(atl, 1),
        "form_tsb_pre": round(tsb_pre, 1),
        "form_tsb_post": round(tsb_post, 1),
        "form_label": form_label(tsb_pre),
        "ctl_ramp_7d": ramp,
        "days_of_history": days_of_history,
        "warming_up": warming_up,
        "seed_value": round(seed, 1),
    }


if __name__ == "__main__":
    # Smoke test against the committed example history.
    import json
    import config

    with open(config.PROJECT_ROOT / "data" / "recent_activities.example.json", encoding="utf-8") as f:
        recent = json.load(f)
    state = compute_load(recent, "2026-01-06", activity_tss=100)
    print(json.dumps(state, indent=2))
