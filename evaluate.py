"""Evaluation harness (eval layer, part 2) — measure analysis quality so a
prompt or model change is a decision, not a guess.

For each frozen case in evals/cases/, it:
  1. runs the case through the REAL coach (dry_run — no history writes),
  2. asks a SECOND Claude call (LLM-as-judge) to score the output against
     evals/rubric.md,
  3. prints per-case scores + an overall, and logs them to runs.db.

Because the prompt is hashed (coach.PROMPT_VERSION), each eval is tagged with the
exact prompt that produced it — so you can change a prompt, re-run, and compare.

    python evaluate.py
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

import config
import runlog
import training_load
from coach import run_analysis, PROMPT_VERSION, _weekday
from llm import get_llm_client
from plan_loader import get_training_plan, get_recent_activities

CASES_DIR = config.PROJECT_ROOT / "evals" / "cases"
RUBRIC_PATH = config.PROJECT_ROOT / "evals" / "rubric.md"

JUDGE_SYSTEM = (
    "You are a strict, fair evaluator of an endurance coach's activity analysis. "
    "Score the analysis against the rubric criteria. Reward specific, correct, "
    "grounded analysis; penalize vague platitudes and factual errors (wrong "
    "weekday, fabricated metrics, force-grading an unplanned activity, malformed "
    "numbers like '11:81'). Output ONLY a JSON object — no prose, no code fences."
)


def _strip_notes(d: dict) -> dict:
    """Drop documentation keys ('_note', ...) so neither the coach nor the judge
    sees the expected answer — that would inflate the score."""
    return {k: v for k, v in d.items() if not k.startswith("_")}


def _extract_json(text: str) -> dict:
    """Parse a JSON object out of the judge's reply, tolerating code fences."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 1)[1]
        if t.lstrip().startswith("json"):
            t = t.lstrip()[4:]
        t = t.rsplit("```", 1)[0]
    return json.loads(t.strip())


def judge(activity: dict, analysis: str, rubric: str, plan: dict, recent: list,
          correct_weekday: str, load_state: dict | None) -> dict:
    """LLM-as-judge: score one analysis against the rubric. Returns the parsed
    JSON verdict.

    The judge is given the SAME context the coach had — plan, recent history, the
    code-computed weekday, and the derived training-load state — so it can verify
    grounding, dates, and readiness fairly rather than flagging data it wasn't
    shown. (Form/TSB is derived, not in the activity JSON, so without this the
    judge couldn't check criterion 9.)
    """
    load_block = (
        f"# Training-load state the coach was given (authoritative — use it to score readiness)\n"
        f"```json\n{json.dumps(load_state, indent=2, default=str)}\n```\n\n"
        if load_state else ""
    )
    user = (
        "# Rubric\n" + rubric + "\n\n"
        "# Athlete's training plan\n"
        f"```json\n{json.dumps(plan, indent=2)}\n```\n\n"
        "# Recent activity history (the coach had this too — use it to verify grounding)\n"
        f"```json\n{json.dumps(recent, indent=2, default=str)}\n```\n\n"
        f"# Correct weekday for this activity (computed reliably): {correct_weekday}\n\n"
        + load_block +
        "# The activity that was analyzed\n"
        f"```json\n{json.dumps(activity, indent=2, default=str)}\n```\n\n"
        "# The coach's analysis to score\n" + analysis + "\n\n"
        "Score each rubric criterion 0-2. Output ONLY this JSON:\n"
        '{"criteria": [{"name": "...", "score": N, "reason": "..."}], '
        '"total": N, "max_total": 18, "verdict": "one-sentence summary"}'
    )
    # Judge is pinned to a FIXED model so scores stay comparable when we vary
    # the coach's model — otherwise a "better" score could just be a softer judge.
    return _extract_json(get_llm_client(config.EVAL_JUDGE_MODEL).complete(JUDGE_SYSTEM, user).text)


# Models the --compare flag benchmarks by default (override with --models).
DEFAULT_COMPARE_MODELS = ["claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5"]


def _check_proposal(expect, proposals: list) -> tuple[bool, str, str]:
    """Deterministic proposal-decision check. `expect` is the case's
    `_expect_proposal` (falsy = should NOT propose; truthy/dict = should propose,
    optionally of a given `type`). Returns (correct?, expected_str, got_str).

    This is behavioral correctness — checked in code, not by the LLM judge — so
    over-proposing (fires on a normal day) and under-proposing (misses a clear
    flag) are caught precisely and without eval noise."""
    made = len(proposals) > 0
    expected = bool(expect)
    exp_type = expect.get("type") if isinstance(expect, dict) else None
    correct = (made == expected)
    if expected and made and exp_type:
        correct = correct and any(p.get("type") == exp_type for p in proposals)
    expected_str = (exp_type or "yes") if expected else "none"
    got_str = (proposals[0].get("type", "?") if made else "none")
    return correct, expected_str, got_str


def run_eval(coach_model, rubric, plan, recent, cases, verbose=True):
    """Run every case under `coach_model` (judge stays fixed), score + log each.

    Returns (total_score, max_score, telemetry, proposal_correct, proposal_total).
    Telemetry is avg tokens + latency for this model's runs; the proposal counts
    are the deterministic propose-or-not accuracy (separate from the rubric).
    """
    since = datetime.now().isoformat(timespec="seconds")
    total = max_total = 0.0
    prop_correct = prop_total = 0
    for path in cases:
        name = path.stem
        raw = json.loads(path.read_text(encoding="utf-8"))
        activity = _strip_notes(raw)
        # A case may pin an explicit training-load state (to test a controlled
        # readiness scenario); otherwise derive it from history like a real run.
        # Compute once and give the SAME state to coach and judge so they agree.
        load_state = raw.get("_load_state") or training_load.compute_load(
            recent, activity.get("date"), activity_tss=activity.get("load_tss")
        )
        capture: dict = {}
        try:
            analysis = run_analysis(activity, verbose=False, dry_run=True, model=coach_model, load_state=load_state, capture=capture)
            verdict = judge(activity, analysis, rubric, plan, recent, _weekday(activity.get("date")), load_state)
        except Exception as exc:
            # e.g. a model this account can't access — skip it, don't crash the batch.
            if verbose:
                print(f"  {name:<22} FAILED: {exc}")
            continue
        t = float(verdict.get("total", 0))
        m = float(verdict.get("max_total", 18))
        total += t
        max_total += m
        ok, exp_str, got_str = _check_proposal(raw.get("_expect_proposal"), capture.get("proposals", []))
        prop_total += 1
        prop_correct += 1 if ok else 0
        runlog.log_eval(name, PROMPT_VERSION, coach_model, t, m, verdict.get("verdict", ""), json.dumps(verdict))
        if verbose:
            pct = (100 * t / m) if m else 0
            mark = "ok" if ok else "XX"
            print(
                f"  {name:<22} {t:.0f}/{m:.0f}  ({pct:.0f}%)  "
                f"proposal[{mark}] exp={exp_str}/got={got_str}  {verdict.get('verdict', '')}"
            )
    return total, max_total, runlog.model_stats(coach_model, since), prop_correct, prop_total


def main() -> None:
    parser = argparse.ArgumentParser(description="Score analyses against the rubric with an LLM judge.")
    parser.add_argument("--compare", action="store_true", help="Benchmark a default set of Claude models.")
    parser.add_argument("--models", help="Comma-separated model ids to benchmark (implies compare mode).")
    args = parser.parse_args()

    rubric = RUBRIC_PATH.read_text(encoding="utf-8")
    plan = get_training_plan()
    recent = get_recent_activities()
    cases = sorted(CASES_DIR.glob("*.json"))
    if not cases:
        print("No eval cases found in evals/cases/.")
        return

    models = None
    if args.models:
        models = [m.strip() for m in args.models.split(",") if m.strip()]
    elif args.compare:
        models = DEFAULT_COMPARE_MODELS

    # --- Compare mode: run every case under each model; judge held fixed. ---
    if models:
        print(
            f"Benchmarking {len(models)} models on {len(cases)} cases | prompt {PROMPT_VERSION} "
            f"| judge {config.EVAL_JUDGE_MODEL} (fixed)\n"
        )
        rows = []
        for model in models:
            print(f"  running {model} ...")
            rows.append((model, *run_eval(model, rubric, plan, recent, cases, verbose=False)))

        print(f"\n{'model':<20}{'score':>16}{'proposals':>11}{'avg tokens':>13}{'avg latency':>13}")
        print("-" * 73)
        for model, total, max_total, stats, pc, pt in rows:
            pct = f"{100 * total / max_total:.0f}% ({total:.0f}/{max_total:.0f})" if max_total else "-"
            prop = f"{pc}/{pt}" if pt else "-"
            tok = f"{stats['avg_tokens']:.0f}" if stats["avg_tokens"] else "-"
            lat = f"{stats['avg_latency']:.1f}s" if stats["avg_latency"] else "-"
            print(f"{model:<20}{pct:>16}{prop:>11}{tok:>13}{lat:>13}")
        print(f"\nprompt {PROMPT_VERSION} | logged to runs.db. Pick your spot on the quality/cost/speed curve.")
        return

    # --- Single-model mode: the configured coach model. ---
    print(
        f"Evaluating {len(cases)} cases | prompt {PROMPT_VERSION} | coach {config.CLAUDE_MODEL} "
        f"| judge {config.EVAL_JUDGE_MODEL}\n"
    )
    total, max_total, stats, pc, pt = run_eval(config.CLAUDE_MODEL, rubric, plan, recent, cases, verbose=True)
    if max_total:
        print(
            f"\nOVERALL: {total:.0f}/{max_total:.0f} ({100 * total / max_total:.0f}%) "
            f"| proposals {pc}/{pt} correct "
            f"| avg {(stats['avg_tokens'] or 0):.0f} tokens, {(stats['avg_latency'] or 0):.1f}s "
            f"| prompt {PROMPT_VERSION} | model {config.CLAUDE_MODEL}"
        )
        print("Logged to runs.db. Change a prompt or model, re-run, compare.")


if __name__ == "__main__":
    main()
