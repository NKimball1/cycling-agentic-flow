# ADR 0003 — A separate adjustment ledger (don't mutate the plan)

- **Status:** Accepted
- **Date:** 2026-08-14
- **Context area:** agent actions, safety, auditability

## Context

The coach can *act*: when the data clearly warrants it, it proposes a change to
the upcoming training plan (see the propose → approve gate in
`plan_adjustments.py`). That raises a question — where does an approved change
live? Two options:

1. **Mutate the plan.** Edit `training_plan.json` in place when a proposal is
   approved.
2. **A separate ledger.** Record proposals in their own store
   (`plan_adjustments.json`), each with a status, and have the coach read the plan
   *and* the active adjustments together.

## Decision

**A separate ledger.** The agent never edits `training_plan.json`. Proposals are
appended to `plan_adjustments.json` as records with a `status`
(`pending → approved | rejected`); the coach merges the base plan with the active
adjustments at read time.

## Why

- **The plan is a stable weekly *template*; adjustments are dated, one-off
  deviations.** Folding a one-off ("make *this* Thursday easy") into a recurring
  template is a category error — it would corrupt the template for every future
  week.
- **Auditability.** Every proposal keeps its who/what/why/when and its outcome. You
  can see what the agent suggested and what you decided, not just the end state.
- **Reversibility.** Approve / reject / revert is a status flip, never a destructive
  edit. Nothing has to be "un-merged" from the plan.
- **The source of truth stays authoritative.** The base plan is only ever changed
  by a human editing the file. The agent proposes into a separate store — a clean
  safety boundary: an agentic action can *suggest* but cannot silently rewrite the
  thing it's being measured against.

## Consequences

- The coach must **merge** template + active adjustments when building context
  (`plan_adjustments.for_context` + `coach._adjustment_facts`): honor approved ones,
  and don't re-pitch pending or already-rejected ones.
- **Dedup lives at two layers:** code-level (`propose` won't create a duplicate
  pending/approved entry for the same date+type) and prompt-level (the coach is
  shown what's already on record so it doesn't re-propose). Rejected-but-still-future
  items are surfaced too, so a declined idea isn't re-pitched (ADR-adjacent fix).
- Past-dated adjustments naturally drop out of context — you can't adjust a session
  that already happened.

## Revisit this if…

- Approved adjustments frequently represent *permanent* plan changes (not one-offs).
  Then add an explicit, human-run "fold approved adjustments into the template" step
  — still deliberate, never automatic.
- The ledger grows large or is written concurrently enough that the JSON file's
  small read/write race matters — move it to SQLite (already used for the run log).

## The general lesson

For an agent that takes actions, **separate the proposal/record from the source of
truth.** It keeps the authoritative artifact human-owned, makes every action
auditable and reversible, and turns "the agent changed my plan" into "the agent
suggested a change I approved" — which is the property you actually want from a
system that acts on your behalf.
