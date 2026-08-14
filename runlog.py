"""SQLite run log — observability for every analysis (eval layer, part 1).

Every analysis records what went in, what came out, which prompt/model produced
it, and how many tokens / how long it took. This is the raw data that makes
quality *measurable*: without a record of what happened, you're changing prompts
on vibes. SQLite because it's a single file with no server — perfect for the box.

Two tables:
  runs  — one row per analysis (real cron runs, manual tests, and eval runs).
  evals — one row per scored eval case (written by evaluate.py).

View recent runs:  python runlog.py
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT,
    source         TEXT,     -- 'fit' or 'strava'
    sport          TEXT,
    activity_date  TEXT,
    model          TEXT,
    prompt_version TEXT,     -- hash of the system prompt that produced this
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    latency_s      REAL,
    dry_run        INTEGER,  -- 1 for tests/evals, 0 for real runs
    analysis       TEXT,     -- the full markdown output
    activity_json  TEXT      -- the input metrics (for inspection / replay)
);
CREATE TABLE IF NOT EXISTS evals (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT,
    case_name      TEXT,
    prompt_version TEXT,
    model          TEXT,
    score          REAL,
    max_score      REAL,
    verdict        TEXT,
    judge_json     TEXT      -- the judge's full per-criterion breakdown
);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.RUNS_DB_PATH)
    conn.executescript(_SCHEMA)
    return conn


def log_run(activity: dict, result, latency_s: float, dry_run: bool, prompt_version: str) -> None:
    """Record one analysis. `result` is an llm.LLMResult (duck-typed here)."""
    with _conn() as conn:
        conn.execute(
            "INSERT INTO runs (ts, source, sport, activity_date, model, prompt_version, "
            "input_tokens, output_tokens, latency_s, dry_run, analysis, activity_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                datetime.now().isoformat(timespec="seconds"),
                activity.get("source"),
                activity.get("sport"),
                (activity.get("date") or "")[:10],
                getattr(result, "model", ""),
                prompt_version,
                getattr(result, "input_tokens", 0),
                getattr(result, "output_tokens", 0),
                latency_s,
                1 if dry_run else 0,
                getattr(result, "text", ""),
                json.dumps(activity, default=str),
            ),
        )


def log_eval(case_name, prompt_version, model, score, max_score, verdict, judge_json) -> None:
    """Record one scored eval case (called by evaluate.py)."""
    with _conn() as conn:
        conn.execute(
            "INSERT INTO evals (ts, case_name, prompt_version, model, score, max_score, verdict, judge_json) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                datetime.now().isoformat(timespec="seconds"),
                case_name,
                prompt_version,
                model,
                score,
                max_score,
                verdict,
                judge_json,
            ),
        )


def model_stats(model: str, since_ts: str) -> dict:
    """Average total tokens + latency for a model's runs since `since_ts` (ISO).

    Used by the benchmark to read cost/speed for each model straight from the run
    log — the observability data is exactly what the comparison needs.
    """
    with _conn() as conn:
        avg_tokens, avg_latency, n = conn.execute(
            "SELECT AVG(input_tokens + output_tokens), AVG(latency_s), COUNT(*) "
            "FROM runs WHERE model = ? AND ts >= ? AND dry_run = 1",
            (model, since_ts),
        ).fetchone()
    return {"avg_tokens": avg_tokens, "avg_latency": avg_latency, "n": n}


def recent_runs(limit: int = 15):
    with _conn() as conn:
        return conn.execute(
            "SELECT ts, source, sport, model, input_tokens, output_tokens, latency_s, dry_run "
            "FROM runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()


if __name__ == "__main__":
    rows = recent_runs()
    if not rows:
        print("No runs logged yet. Run an analysis (sync.py / analyze.py) first.")
    else:
        print(f"{'when':<20}{'src':<8}{'sport':<10}{'model':<16}{'in':>7}{'out':>7}{'sec':>7}  test")
        for ts, source, sport, model, tin, tout, lat, dry in rows:
            print(
                f"{ts:<20}{(source or '-'):<8}{(sport or '-'):<10}{(model or '-'):<16}"
                f"{tin or 0:>7}{tout or 0:>7}{lat or 0:>7}  {'Y' if dry else ''}"
            )
