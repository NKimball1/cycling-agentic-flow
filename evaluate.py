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

import json

import config
import runlog
from coach import run_analysis, PROMPT_VERSION
from llm import get_llm_client
from plan_loader import get_training_plan

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


def judge(activity: dict, analysis: str, rubric: str, plan: dict) -> dict:
    """LLM-as-judge: score one analysis against the rubric. Returns the parsed
    JSON verdict."""
    user = (
        "# Rubric\n" + rubric + "\n\n"
        "# Athlete's training plan (context for judging plan-fit)\n"
        f"```json\n{json.dumps(plan, indent=2)}\n```\n\n"
        "# The activity that was analyzed\n"
        f"```json\n{json.dumps(activity, indent=2, default=str)}\n```\n\n"
        "# The coach's analysis to score\n" + analysis + "\n\n"
        "Score each rubric criterion 0-2. Output ONLY this JSON:\n"
        '{"criteria": [{"name": "...", "score": N, "reason": "..."}], '
        '"total": N, "max_total": 14, "verdict": "one-sentence summary"}'
    )
    return _extract_json(get_llm_client().complete(JUDGE_SYSTEM, user).text)


def main() -> None:
    rubric = RUBRIC_PATH.read_text(encoding="utf-8")
    plan = get_training_plan()
    cases = sorted(CASES_DIR.glob("*.json"))
    if not cases:
        print("No eval cases found in evals/cases/.")
        return

    print(f"Evaluating {len(cases)} cases | prompt {PROMPT_VERSION} | model {config.CLAUDE_MODEL}\n")
    grand = grand_max = 0.0
    for path in cases:
        name = path.stem
        activity = _strip_notes(json.loads(path.read_text(encoding="utf-8")))
        analysis = run_analysis(activity, verbose=False, dry_run=True)
        try:
            verdict = judge(activity, analysis, rubric, plan)
        except Exception as exc:
            print(f"  {name:<22} JUDGE FAILED: {exc}")
            continue
        total = float(verdict.get("total", 0))
        max_total = float(verdict.get("max_total", 14))
        grand += total
        grand_max += max_total
        runlog.log_eval(
            name, PROMPT_VERSION, config.CLAUDE_MODEL, total, max_total,
            verdict.get("verdict", ""), json.dumps(verdict),
        )
        pct = (100 * total / max_total) if max_total else 0
        print(f"  {name:<22} {total:.0f}/{max_total:.0f}  ({pct:.0f}%)  {verdict.get('verdict', '')}")

    if grand_max:
        print(
            f"\nOVERALL: {grand:.0f}/{grand_max:.0f} ({100 * grand / grand_max:.0f}%) "
            f"| prompt {PROMPT_VERSION} | model {config.CLAUDE_MODEL}"
        )
        print("Logged to runs.db. Change a prompt, re-run, and compare the number.")


if __name__ == "__main__":
    main()
