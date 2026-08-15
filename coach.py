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
from datetime import datetime, timedelta

import plan_adjustments
import runlog
import training_load
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


def _calendar_facts(activity_date: str, back_days: int = 21, forward_days: int = 28) -> str:
    """A weekday calendar for the weeks around the activity, so the model can look
    up the weekday of ANY date it wants to mention — a plan milestone (block
    start), an upcoming session, a past ride — instead of computing it.

    Parsing the plan's prose dates ("Aug 18-31") in code is fuzzy and risks
    injecting a wrong "authoritative" fact; a calendar is pure, reliable date
    math and covers every date in range, whatever the model references.
    """
    try:
        d = datetime.strptime((activity_date or "")[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return ""
    labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    start = d - timedelta(days=back_days)
    start -= timedelta(days=start.weekday())  # snap back to Monday
    end = d + timedelta(days=forward_days)
    base_year = d.year
    rows = []
    week = start
    while week <= end:
        cells = []
        for i in range(7):
            day = week + timedelta(days=i)
            stamp = day.strftime("%m-%d") if day.year == base_year else day.isoformat()
            cells.append(f"{labels[i]} {stamp}")
        rows.append("- " + " | ".join(cells))
        week += timedelta(days=7)
    return (
        f"## Weekday calendar (all dates {base_year} unless a year is shown; "
        "authoritative — look up any date's weekday here, do NOT compute it)\n"
        + "\n".join(rows) + "\n\n"
    )


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


def _adjustment_facts(as_of_date: str | None) -> str:
    """Surface plan adjustments in play for upcoming dates: APPROVED ones the
    coach must honor over the base template, and PENDING ones already proposed
    (so it doesn't propose the same thing again)."""
    items = plan_adjustments.for_context(as_of_date)
    if not items:
        return ""
    lines = ["## Plan adjustments in effect (honor APPROVED ones over the base weekly template)"]
    for a in items:
        lines.append(
            f"- [{a.get('status', '?').upper()}] {a.get('for_date', '?')} — "
            f"{a.get('summary', '')} ({a.get('type', '?')}, {a.get('impact', '?')})"
        )
    lines.append(
        "Do NOT re-propose an adjustment that already appears here (approved or pending)."
    )
    return "\n".join(lines) + "\n\n"


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


def _load_facts(state: dict | None) -> str:
    """Format a training-load state (from training_load.compute_load) into the
    facts block the model reads. The model reads the state but never recomputes
    the rolling averages. Returns "" when there's no state to show."""
    if not state:
        return ""
    lines = [
        "## Training-load state (Fitness / Fatigue / Form — computed from your history)",
        f"- Fitness (CTL, 42-day): {state['fitness_ctl']} (current, incl. this activity)",
        f"- Fatigue (ATL, 7-day): {state['fatigue_atl']} (current, incl. this activity)",
        f"- Form (TSB): {state['form_tsb_pre']} going INTO this session -> "
        f"{state['form_tsb_post']} after ({state['form_label']})",
    ]
    if state["ctl_ramp_7d"] is not None:
        trend = "building" if state["ctl_ramp_7d"] > 0 else "detraining" if state["ctl_ramp_7d"] < 0 else "flat"
        lines.append(f"- 7-day fitness trend (CTL ramp): {state['ctl_ramp_7d']:+} ({trend})")
    if state["warming_up"]:
        lines.append(
            f"- NOTE: based on only {state['days_of_history']} days of history "
            f"(seeded at {state['seed_value']}); CTL is still warming up — treat "
            f"fitness as an approximate floor, not an exact figure."
        )
    return "\n".join(lines) + "\n\n"

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

Training-load state is computed for you and provided: Fitness (CTL, a 42-day \
average of load), Fatigue (ATL, a 7-day average), and Form (TSB = Fitness − \
Fatigue). Read Form as readiness: clearly positive means fresh/tapered, mildly \
negative is normal productive training, and deeply negative (roughly below −30) \
signals accumulated strain worth respecting. Use the "Form going INTO this \
session" value to judge whether the athlete was rested or fatigued for THIS \
effort (e.g. a strong session on tired legs is more impressive; a flat session \
on fresh legs is worth a closer look), and the 7-day fitness trend to say \
whether they're building, holding, or detraining. Do NOT recompute these \
numbers — quote the provided values. When the note says the history is still \
warming up, treat Fitness as an approximate floor and don't over-interpret its \
exact value.

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
provided day counts rather than deriving them. A weekday calendar for the \
surrounding weeks is also provided: to state the weekday of ANY date — a plan \
milestone like a block start, an upcoming session, or a past activity — look it \
up in that calendar. Never compute or guess a weekday yourself.

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

Proposing a plan change (optional, and the exception — not every analysis): if \
the evidence clearly warrants adapting the UPCOMING plan — deeply negative form \
before a quality day, a repeated pattern the plan should absorb, an \
illness/injury or overreach signal — you may call `propose_plan_adjustment` \
ONCE to propose a single, specific change. It is a PROPOSAL the athlete must \
approve; it does not change the plan, so frame it as a suggestion, not a done \
deal. Prefer small, reversible (tier-0) changes. Most days need no adjustment — \
if the training is on track, do NOT propose one. Any adjustments already listed \
as in effect are authoritative: honor approved ones, and never re-propose an \
adjustment that is already approved or pending.

After writing the analysis (and the optional proposal), call the `log_activity` \
tool exactly once to record this activity in the athlete's history, including a \
short coach note in the style of a training log. log_activity is your final \
action — do not write any text after it."""

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

# The action tool: PROPOSE (not apply) a change to the upcoming plan. This is the
# agent's one lever to act — and it's gated. Calling it records a PENDING proposal
# for the athlete to approve; it never edits the plan. It is conditional: the
# model calls it only when the evidence clearly warrants an adjustment, which is
# the exception, not the rule.
PROPOSE_PLAN_ADJUSTMENT_TOOL = {
    "name": "propose_plan_adjustment",
    "description": (
        "Propose ONE specific change to the UPCOMING training plan when the "
        "evidence clearly warrants it — e.g. deeply negative form heading into a "
        "quality day, a repeated pattern the plan should adapt to, or an "
        "illness/injury signal. This is a PROPOSAL that requires the athlete's "
        "approval; it does NOT change the plan. Most analyses need no adjustment, "
        "so usually you will NOT call this. Never propose more than one, and only "
        "when you can justify it from the data. Call this BEFORE log_activity."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "for_date": {
                "type": "string",
                "description": "Date (YYYY-MM-DD) the change applies to. Use the weekday calendar to pick it.",
            },
            "type": {
                "type": "string",
                "description": "Kind of change: reduce_intensity, move_session, add_rest, swap_sessions, extend_recovery, or note.",
            },
            "summary": {
                "type": "string",
                "description": "One concrete sentence: exactly what to change (e.g. 'Swap Thursday VO2 for easy Z2').",
            },
            "rationale": {
                "type": "string",
                "description": "Why, grounded in the athlete's data — form/TSB, load, recent sessions, the plan's intent.",
            },
            "impact": {
                "type": "string",
                "description": "'tier-0' for a small reversible tweak (shift/swap/rest); 'tier-1' for a structural change (alter a block or targets). Prefer tier-0.",
            },
        },
        "required": ["for_date", "type", "summary", "rationale"],
    },
}


def run_analysis(
    activity: dict,
    verbose: bool = True,
    dry_run: bool = False,
    model: str | None = None,
    load_state: dict | None = None,
    capture: dict | None = None,
) -> str:
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

    # Rolling load state (Fitness/Fatigue/Form), derived from history. Callers
    # (the eval harness) may pass an explicit state to test a controlled
    # scenario; real runs compute it here.
    if load_state is None:
        load_state = training_load.compute_load(
            recent, activity.get("date"), activity_tss=activity.get("load_tss")
        )

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
        f"{_calendar_facts(activity_date)}"
        f"{_load_facts(load_state)}"
        f"{_adjustment_facts(activity_date)}"
        f"{_weather_facts(activity)}"
        "## Activity to analyze (metrics)\n"
        f"```json\n{json.dumps(activity, indent=2, default=str)}\n```\n\n"
        "## Training plan\n"
        f"```json\n{json.dumps(plan, indent=2)}\n```\n\n"
        "## Recent activities (all sports, most recent first)\n"
        f"```json\n{json.dumps(recent, indent=2)}\n```\n\n"
        "Write the analysis using the required sections, then call log_activity."
    )

    # Executes the tools the model requests: the history write (log_activity)
    # and the plan-adjustment proposal. Both are no-ops in dry_run. New proposals
    # are collected so we can surface them for approval in the delivered text.
    proposal_calls: list[dict] = []   # every propose call (captured for eval too)
    proposed_this_run: list[dict] = []  # newly-recorded proposals (for the footer)

    def tool_executor(name: str, tool_input: dict) -> dict:
        if name == "propose_plan_adjustment":
            proposal_calls.append(tool_input)
            if dry_run:
                if verbose:
                    print("  [dry-run] propose_plan_adjustment called; NOT recorded:")
                    print(json.dumps(tool_input, indent=2))
                return {"status": "dry_run", "detail": "proposal not recorded"}
            result = plan_adjustments.propose(tool_input)
            if result.get("status") == "proposed":
                proposed_this_run.append({**tool_input, "id": result.get("id")})
            if verbose:
                print(f"  Plan adjustment {result.get('status')}: {result.get('id')}")
            return result
        if name == "log_activity":
            if dry_run:
                if verbose:
                    print("  [dry-run] log_activity called; entry NOT written:")
                    print(json.dumps(tool_input, indent=2))
                return {"status": "dry_run", "detail": "entry not written to history"}
            result = append_activity(tool_input)
            if verbose:
                print(f"  Logged to history: {result}")
            return result
        return {"error": f"unknown tool {name}"}

    # The provider seam runs the tool-use loop and returns the analysis + token
    # telemetry. log_activity is a terminal side effect (record and done), so we
    # stop as soon as it runs — no wasted follow-up turn, no post-tool text.
    client = get_llm_client(model)
    started = time.monotonic()
    result = client.run_tool_loop(
        system=SYSTEM_PROMPT,
        user_message=user_message,
        tools=[PROPOSE_PLAN_ADJUSTMENT_TOOL, LOG_ACTIVITY_TOOL],
        tool_executor=tool_executor,
        verbose=verbose,
        # log_activity ends the loop; propose_plan_adjustment does not, so the
        # model can propose an adjustment and then still log the activity.
        terminal_tools={"log_activity"},
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

    # Surface any new proposal for approval, appended to the delivered analysis.
    text_out = result.text
    if proposed_this_run:
        lines = ["\n\n---", "**⚠️ Proposed plan adjustment — needs your approval:**"]
        for p in proposed_this_run:
            lines.append(f"- {p.get('summary', '')} (for {p.get('for_date', '?')}) — id `{p.get('id')}`")
        lines.append(
            "Review: `python plan_adjustments.py`  ·  approve: "
            "`python plan_adjustments.py --approve <id>`"
        )
        text_out += "\n".join(lines)

    # Expose the proposal decision to callers that ask (the eval harness), so
    # propose-or-not can be scored deterministically against the case's label.
    if capture is not None:
        capture["proposals"] = proposal_calls

    return text_out
