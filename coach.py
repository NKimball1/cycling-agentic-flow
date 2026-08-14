"""The analysis step: gather context, analyze, and log the activity.

Gathering the inputs is still deterministic plain code — we always need the
activity, the plan, and the recent history, so there's no decision to hand the
model there.

What IS handed to a tool is the *write*: after Claude analyzes the activity, it
calls `log_activity` with a structured entry, and our code appends it to the
history file. The model doesn't decide *whether* to log (it always does) — the
tool exists to give a side-effecting write a typed, validated, code-controlled
boundary. That's the second reason tools earn their place (the first, selective
retrieval, arrives with Strava in Phase 2). So there's a minimal tool-use loop
here now, and it's justified.

This module owns the *what* — the system prompt, the tool definition, and the
analyze-then-log flow. The *how* (which LLM, its tool-call dialect, the loop
mechanics) lives behind the provider seam in llm.py, so coach.py is
provider-agnostic: it never imports a vendor SDK.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime

import runlog
from llm import get_llm_client
from plan_loader import get_training_plan, get_recent_activities, append_activity


def _weekday(date_str: str | None) -> str | None:
    """Weekday name for a 'YYYY-MM-DD...' date string, computed reliably in code.

    LLMs are unreliable at deriving weekdays and doing date arithmetic (it's
    computation, not language), so we compute these facts here and hand them to
    the model instead of making it reason about the calendar.
    """
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d").strftime("%A")
    except (ValueError, TypeError):
        return None


def _days_before(reference: str | None, other: str | None) -> int | None:
    """Whole days that `other` falls before `reference` (both 'YYYY-MM-DD...').

    Positive = `other` is before the reference, 0 = same day, negative = after.
    Returns None if either date can't be parsed. Date subtraction is arithmetic,
    not language — so we do it here and hand the model the answer.
    """
    try:
        ref = datetime.strptime((reference or "")[:10], "%Y-%m-%d")
        oth = datetime.strptime((other or "")[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None
    return (ref - oth).days


def _timing_facts(activity_date: str, recent: list) -> str:
    """Pre-compute how long before (or after) THIS activity each recent activity
    happened, so the model never has to subtract dates itself.

    This closes a real bug the eval surfaced: the coach reasons well about
    single-activity dates (weekday is injected) but fumbles inter-activity gaps
    like "the hard ride two days ago" when left to compute them. We give it those
    gaps as authoritative facts instead.
    """
    lines = []
    for a in recent:
        d = a.get("date")
        delta = _days_before(activity_date, d)
        if delta is None:
            continue
        weekday = _weekday(d)
        weekday_s = f" {weekday}" if weekday else ""
        name = a.get("name") or a.get("type") or a.get("sport") or "activity"
        if delta > 0:
            rel = f"{delta} day{'s' if delta != 1 else ''} before this activity"
        elif delta == 0:
            rel = "same day as this activity"
        else:
            n = -delta
            rel = f"{n} day{'s' if n != 1 else ''} after this activity"
        lines.append(f"- {d}{weekday_s} — {name}: {rel}")
    if not lines:
        return ""
    return (
        "### Recent activity timing (days relative to this activity — authoritative, "
        "do not recompute)\n" + "\n".join(lines) + "\n\n"
    )


def _weather_facts(activity: dict) -> str:
    """A compact, human-readable weather line for the model, when weather was
    resolved for this activity. Measured conditions (from weather.py) — the model
    interprets them (heat drift, wind, cold) but never invents them."""
    w = activity.get("weather")
    if not w:
        return ""
    parts = []
    if w.get("temp_f") is not None:
        parts.append(f"{w['temp_f']:.0f}°F")
    if w.get("apparent_temp_f") is not None:
        parts.append(f"feels {w['apparent_temp_f']:.0f}°F")
    if w.get("humidity_pct") is not None:
        parts.append(f"{w['humidity_pct']:.0f}% humidity")
    if w.get("wind_mph") is not None:
        parts.append(f"{w['wind_mph']:.0f} mph wind")
    if w.get("precip_in"):
        parts.append(f"{w['precip_in']:.2f} in precip")
    if w.get("conditions"):
        parts.append(str(w["conditions"]))
    if not parts:
        return ""
    return "## Weather at the start (measured, not estimated)\n- " + ", ".join(parts) + "\n\n"

SYSTEM_PROMPT = """You are an experienced endurance coach analyzing a single \
training activity for an athlete whose primary sport is cycling but who also \
runs and walks. Every activity — ride, run, or walk — counts toward the plan \
and toward fatigue, so weigh them together. Units are imperial (miles, feet), \
power in watts, heart rate in bpm.

Reading training load:
- `power_tss` is cycling load from power vs FTP (rides with a meter only).
- `hr_tss` is heart-rate load and is comparable across ALL sports.
- `load_tss` is the single number to compare activities on, and `load_source`
  tells you how it was derived: "power" (most reliable), "hr" (an estimate — HR
  lags effort and drifts with heat/fatigue), or "estimated" (a rough
  duration-based guess used only when there's no power AND no HR). When
  load_source is "estimated", use the number but call it an estimate and don't
  over-interpret its precision; never invent a load figure of your own.

Reading pace: quote `avg_pace` (already formatted, e.g. "11:49" per mile). \
`avg_pace_min_per_mi` is decimal minutes for math only — never render it as a \
clock time.

Weather at the start (temperature, apparent "feels-like" temperature, humidity, \
wind, precipitation, conditions) is MEASURED and provided when available. Use it \
to explain physiology and execution: heat and humidity raise HR and drive \
cardiac drift/decoupling (so a higher HR at the same power may be the weather, \
not lost fitness); wind distorts pace and power-vs-speed; cold changes warmup and \
early effort. Reference weather only when it is provided — never invent \
conditions, and if none is given, don't speculate about it.

Planned vs. unplanned: not every activity is a prescribed workout. Some are just \
life — an easy walk, a hike, casual cross-training. Before comparing an activity \
to the plan, judge whether it actually corresponds to a prescribed session (by \
sport, intensity, and the plan's weekly structure). If it does NOT, treat it as \
unplanned or supplemental: acknowledge it, account for its load and any recovery \
effect, but do not grade it against a workout it was never meant to be. A walk \
on a rest day is not a missed session, and a hike is not a failed ride.

Dates and weekdays are computed for you and given at the top of the message: \
today's date, this activity's date and weekday, and — under "Recent activity \
timing" — exactly how many days before (or after) this activity each recent \
activity happened. Use these directly; do NOT calculate weekdays or date \
differences yourself — that is error-prone. When you reference recovery windows \
or the spacing between sessions (e.g. "a hard run two days ago"), quote the \
provided day counts rather than deriving them.

You are given three things: the metrics for the activity to analyze, the \
athlete's training plan, and their recent activity history (all sports). \
Produce a structured analysis in Markdown with these sections:

- **Activity Summary** — a 2-3 sentence plain-language recap.
- **Plan vs. Actual** — First decide whether this activity maps to a prescribed
  session. If it does, grade it against the target (duration, intensity, load)
  with specific numbers. If it's unplanned or supplemental (an easy walk, a hike,
  cross-training not in the plan), say so plainly, don't force-fit it to that
  day's prescription, and note what it contributes instead (recovery, easy
  volume, time on feet).
- **Trends & Cross-Sport Load** — patterns versus recent activities, explicitly
  accounting for running/walking load and its effect on cycling recovery (e.g.
  a hard run in the prior 48h). Note decoupling, heat, or Q4 creep if visible.
- **Did You Hit Your Targets?** — a clear verdict when this was a planned session.
  For an unplanned activity there's no target to hit — instead say whether it fits
  the week's intent (an easy walk on a recovery day is fine; a hard hike the day
  before a key session is worth flagging).
- **Coach's Notes** — 2-4 concrete, actionable takeaways for the next few days.

Be direct and specific; use the athlete's real numbers. If a metric is missing, \
say so and work with what's available.

After writing the analysis, call the `log_activity` tool exactly once to record \
this activity in the athlete's history, including a short coach note in the \
style of a training log. Calling the tool is your final action — do not write \
any text after it."""

# A short hash of the system prompt. Any prompt change yields a new version tag,
# so runs and eval scores can be grouped by exactly which prompt produced them —
# that's what makes "did this change help?" answerable.
PROMPT_VERSION = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:8]

# The one tool: a typed, validated boundary for the history write.
LOG_ACTIVITY_TOOL = {
    "name": "log_activity",
    "description": (
        "Record the just-analyzed activity into the athlete's history so future "
        "analyses have it as context. Call this exactly once, after the analysis. "
        "Summarize only the metrics that matter for this sport."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "Activity date as YYYY-MM-DD."},
            "name": {"type": "string", "description": "Short name, e.g. 'Sunday Century'."},
            "type": {"type": "string", "description": "Session label, e.g. 'Long Endurance', 'Run - Easy', 'Threshold'."},
            "sport": {"type": "string", "description": "cycling, running, walking, etc."},
            "load_tss": {"type": "number", "description": "The activity's load_tss (comparable across sports)."},
            "key_metrics": {
                "type": "object",
                "description": (
                    "The most relevant metrics for this sport — e.g. duration, "
                    "distance, avg power/NP/IF for rides; duration, distance, "
                    "pace, avg HR for runs. Include only what's meaningful."
                ),
            },
            "note": {
                "type": "string",
                "description": (
                    "1-3 sentence coach note in the athlete's training-log voice: "
                    "what stood out, execution, patterns (heat cap, Q4 creep, "
                    "decoupling), how it fits the plan."
                ),
            },
        },
        "required": ["date", "name", "type", "sport", "note"],
    },
}


def run_analysis(activity: dict, verbose: bool = True, dry_run: bool = False, model: str | None = None) -> str:
    """Analyze one activity (given its metrics dict), log it, return the Markdown.

    `model` overrides which model runs the analysis (None = the configured
    default). The eval harness uses this to benchmark models on the same task.

    `activity` is a metrics dict from EITHER source — fit_parser (a local .fit)
    or strava (the API). run_analysis is source-agnostic; that's the payoff of
    the shared metrics contract.

    If dry_run is True, the whole flow runs (including Claude calling the
    log_activity tool) but the history file is NOT written — the entry that
    would have been logged is printed instead. Use it to test without mucking
    up data/recent_activities.json.
    """
    # The plan and history are always needed, so gathering them is plain code.
    plan = get_training_plan()
    recent = get_recent_activities()

    if verbose:
        print(
            f"  Analyzing {activity.get('sport')} activity from "
            f"{activity.get('source')}; {len(recent)} activities in history."
        )

    # Compute date facts here (code is reliable at this; the model is not) and
    # inject them prominently so the model never has to derive a weekday.
    today = datetime.now()
    activity_weekday = _weekday(activity.get("date"))
    activity_date = (activity.get("date") or "")[:10] or "unknown"
    date_facts = (
        "## Date facts (computed for you — authoritative; do not recompute)\n"
        f"- Today: {today.strftime('%Y-%m-%d (%A)')}\n"
        f"- This activity's date: {activity_date}"
        f"{f' ({activity_weekday})' if activity_weekday else ''}\n\n"
        + _timing_facts(activity_date, recent)
    )

    user_message = (
        "Analyze this activity, then log it.\n\n"
        f"{date_facts}"
        f"{_weather_facts(activity)}"
        "## Activity to analyze (metrics)\n"
        f"```json\n{json.dumps(activity, indent=2, default=str)}\n```\n\n"
        "## Training plan\n"
        f"```json\n{json.dumps(plan, indent=2)}\n```\n\n"
        "## Recent activities (all sports, most recent first)\n"
        f"```json\n{json.dumps(recent, indent=2)}\n```\n\n"
        "Write the analysis using the required sections, then call log_activity."
    )

    # The one tool: execute the history write (or a no-op in dry_run). The LLM
    # adapter calls this whenever the model requests log_activity.
    def tool_executor(name: str, tool_input: dict) -> dict:
        if name != "log_activity":
            return {"error": f"unknown tool {name}"}
        if dry_run:
            if verbose:
                print("  [dry-run] log_activity called; entry NOT written:")
                print(json.dumps(tool_input, indent=2))
            return {"status": "dry_run", "detail": "entry not written to history"}
        result = append_activity(tool_input)
        if verbose:
            print(f"  Logged to history: {result}")
        return result

    # The provider seam runs the tool-use loop and returns the analysis + token
    # telemetry. log_activity is a terminal side effect (record and done), so we
    # stop as soon as it runs — no wasted follow-up turn, no post-tool text.
    client = get_llm_client(model)
    started = time.monotonic()
    result = client.run_tool_loop(
        system=SYSTEM_PROMPT,
        user_message=user_message,
        tools=[LOG_ACTIVITY_TOOL],
        tool_executor=tool_executor,
        verbose=verbose,
        tools_are_terminal=True,
    )
    latency_s = round(time.monotonic() - started, 2)

    # Observability: record this run. Logging must never break the analysis.
    try:
        runlog.log_run(activity, result, latency_s, dry_run, PROMPT_VERSION)
    except Exception as exc:  # pragma: no cover
        if verbose:
            print(f"  (run-log failed: {exc})")

    if verbose:
        print(
            f"  {result.input_tokens} in + {result.output_tokens} out tokens, "
            f"{latency_s}s"
        )

    return result.text
