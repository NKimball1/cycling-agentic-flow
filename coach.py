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

import json

from llm import get_llm_client
from plan_loader import get_training_plan, get_recent_activities, append_activity

SYSTEM_PROMPT = """You are an experienced endurance coach analyzing a single \
training activity for an athlete whose primary sport is cycling but who also \
runs and walks. Every activity — ride, run, or walk — counts toward the plan \
and toward fatigue, so weigh them together. Units are imperial (miles, feet), \
power in watts, heart rate in bpm.

Reading training load:
- `power_tss` is cycling load from power vs FTP (rides with a meter only).
- `hr_tss` is heart-rate load and is comparable across ALL sports.
- `load_tss` is the single number to compare activities on (power_tss for rides
  with a meter, else hr_tss). hr_tss is an estimate — HR lags effort and drifts
  with heat and fatigue — so treat it as approximate.

Planned vs. unplanned: not every activity is a prescribed workout. Some are just \
life — an easy walk, a hike, casual cross-training. Before comparing an activity \
to the plan, judge whether it actually corresponds to a prescribed session (by \
sport, intensity, and the plan's weekly structure). If it does NOT, treat it as \
unplanned or supplemental: acknowledge it, account for its load and any recovery \
effect, but do not grade it against a workout it was never meant to be. A walk \
on a rest day is not a missed session, and a hike is not a failed ride.

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


def run_analysis(activity: dict, verbose: bool = True, dry_run: bool = False) -> str:
    """Analyze one activity (given its metrics dict), log it, return the Markdown.

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

    user_message = (
        "Analyze this activity, then log it.\n\n"
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

    # The provider seam runs the tool-use loop and returns the final analysis.
    # log_activity is a terminal side effect (record and done), so we stop as
    # soon as it runs — no wasted follow-up turn, no post-tool "Logged." text.
    client = get_llm_client()
    return client.run_tool_loop(
        system=SYSTEM_PROMPT,
        user_message=user_message,
        tools=[LOG_ACTIVITY_TOOL],
        tool_executor=tool_executor,
        verbose=verbose,
        tools_are_terminal=True,
    )
