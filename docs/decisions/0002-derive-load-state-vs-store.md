# ADR 0002 — Derive rolling training-load state from history (don't store it)

- **Status:** Accepted
- **Date:** 2026-08-15
- **Context area:** state management, correctness

## Context

The coach reasons about the athlete's rolling **Fitness / Fatigue / Form**
(CTL / ATL / TSB) — exponentially-weighted averages of daily training load. That
is *stateful*: today's value depends on yesterday's. There are two ways to keep a
rolling number:

1. **Store it.** Persist `{ctl, atl}` and, on each new activity, roll it forward
   one step.
2. **Derive it.** Recompute the whole CTL/ATL curve from the activity history on
   every run.

## Decision

**Derive it.** `training_load.compute_load` rebuilds the series from the stored
activity history each time an analysis runs. Nothing persists a running CTL/ATL.

## Why

The stored approach is fragile in exactly the ways this system gets exercised:

- **Re-analysis double-counts.** Re-running an activity (common during prompt/eval
  iteration, or a manual re-sync) would advance a stored counter twice.
- **Backfills and edits break it.** Adding load to old history entries, or fixing a
  bad one, wouldn't retroactively correct a stored value — it would silently drift.
- **A crash mid-update corrupts the counter.** On a small VM that gets OOM-killed
  (which happened — see the incident postmortem), a half-applied increment is worse
  than no state at all.

Deriving from the history — which is already the source of truth and already
written atomically — is **idempotent and self-healing**: the same inputs always
produce the same state, and fixing the history fixes the state for free. It also
matches the pattern already used for the date facts and weather: compute
deterministic facts in code, hand them to the model.

## Consequences

- **Cost:** the CTL/ATL curve is recomputed each run. It's a cheap day-by-day loop
  over a bounded history window — negligible next to the LLM call.
- **Cold start:** CTL's ~42-day memory needs weeks of history to be accurate.
  Deriving from a short window would read as *falsely fresh*. Handled by **seeding**
  CTL/ATL from the window's average daily load and flagging `warming_up`, so the
  model treats an immature CTL as an approximate floor, not gospel.
- **Robustness proven:** when the real history turned out to have **zero** load
  coverage (schema drift — see `plan_loader.normalize_entry`), the derived state
  was visibly wrong rather than confidently-wrong-but-stored, which is what surfaced
  the data bug in the first place. A stored counter would have hidden it.

## Revisit this if…

- The history grows large enough that recomputing every run is measurably slow
  (then cache the derived series, still keyed off history, rather than storing a
  mutable counter).
- A use case needs the exact historical CTL/ATL *curve* over time (not just the
  current point) frequently enough to justify persisting daily snapshots — as an
  append-only log, still derivable/rebuildable, never an in-place counter.

## The general lesson

Prefer **deriving state from a durable source of truth** over maintaining a mutable
running value, when recomputation is cheap. It buys idempotency, self-healing on
data fixes, and crash-safety — and it turns silent drift into a visible, debuggable
discrepancy.
