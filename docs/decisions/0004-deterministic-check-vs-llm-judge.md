# ADR 0004 — Grade the propose/not decision in code, not with the LLM judge

- **Status:** Accepted
- **Date:** 2026-08-16
- **Context area:** evaluation, measurement

## Context

The eval layer measures two very different things about a coaching run:

1. **Analysis quality** — is the write-up specific, grounded, correctly reasoned?
   Subjective, no single right answer.
2. **The action decision** — did the coach correctly *propose a plan adjustment*
   (or correctly *not*)? A behavioral property with a known-correct answer per case.

The obvious reflex is to grade everything with the same LLM-as-judge rubric. The
question was whether the propose/not decision belongs there too.

## Decision

**Split the measurement by its nature.** Analysis quality is scored by the
LLM-as-judge rubric (`evaluate.py`). The propose-or-not decision is scored by a
**deterministic code check** against a per-case label (`_expect_proposal`): the
harness captures whether the coach actually called `propose_plan_adjustment`
(via `run_analysis(..., capture=...)`) and compares it to the expected outcome.

## Why

- **It has a known-correct answer.** Each eval case declares whether a proposal is
  warranted. Checking "did it propose? should it have?" is exact — no judgment
  needed, so no judge needed.
- **The judge is noisy; a code check isn't.** The LLM judge showed ~5-point
  run-to-run swings on identical inputs. Folding a binary behavioral fact into that
  noise would make it unmeasurable. The deterministic check is stable and free.
- **Goodhart-resistance.** A code check can't be talked around by a persuasive
  analysis; it only cares whether the tool was called.
- **The signal isn't in the text anyway.** The decision happens as a *tool call*,
  not in the analysis prose the judge reads — so capturing it in code is also the
  natural place to observe it.
- **It catches both failure modes precisely:** over-proposing (fires on a normal
  day) and under-proposing (misses a clear fatigue flag).

## Consequences

- The eval reports **two distinct signals** rather than one blended score: a rubric
  percentage (LLM-judged quality) and a proposal-decision accuracy (deterministic).
  Keeping them separate is honest — they measure different kinds of things.
- The judge is still given the *same* derived context the coach had (load state,
  weekday, history) so it grades grounding fairly — but readiness/plan-action
  *behavior* is verified in code.
- Adding a new behavioral expectation is just a label on a case, not a new rubric
  criterion.

## Revisit this if…

- We want to grade proposal **quality**, not just the decision — is the rationale
  grounded, is the target date right, is it appropriately tier-0? That *is*
  subjective, so it would warrant an LLM-judge criterion, layered on top of (not
  replacing) the deterministic decision check.

## The general lesson

**Match the measurement to the property.** Behavioral correctness with a known
answer → a deterministic check (exact, free, noise-free, un-gameable). Nuanced,
subjective quality → an LLM judge. Reaching for the judge on things that have a
right answer just imports its noise and its blind spots — use code where code can.
