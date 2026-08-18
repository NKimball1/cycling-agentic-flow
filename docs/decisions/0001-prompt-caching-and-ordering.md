# ADR 0001 — Prompt caching & context ordering

- **Status:** Accepted (decided not to add prompt caching)
- **Date:** 2026-08-17
- **Context area:** context engineering, cost/latency

## Context

Anthropic prompt caching makes the *order* of a prompt economically load-bearing:
a cache hit on a stable prefix is far cheaper and faster than fresh input tokens,
so the standard advice is to order content by volatility — stable first
(tools, system, reference docs), volatile last (history, the current query) — and
never put something like a timestamp near the top, which invalidates the cache on
every call.

We evaluated whether to apply this to the coach analysis call.

### How our prompt is currently ordered

The API receives the cacheable prefix as `tools` → `system` → `messages`:

1. `tools` — `propose_plan_adjustment`, `log_activity` — **stable**.
2. `system` — `SYSTEM_PROMPT` — **stable** (changes only on a prompt edit; tracked
   by `PROMPT_VERSION`).
3. `messages[0]` — one user message assembled in `coach.run_analysis`, whose
   internal blocks run **volatile-first**: date facts (today's date!) → calendar →
   load state → adjustments → weather → the activity → then the (stable) training
   plan and recent history → the ask.

The two big stable things (tools, system) are already at the front, so the
"timestamp at the top" mistake is avoided *at the prefix level* — today's date
lives inside the user message, after system+tools. Within the user message,
though, the order is volatile-first, which would block caching the (stable) plan.

## The deciding finding

The headline benefit of caching in an agentic loop is prefix reuse across the
loop's multiple LLM calls. **Our loop almost never loops.**

- **Common case (no plan adjustment):** the model writes the full analysis *and*
  emits the terminal `log_activity` tool call in a **single** assistant turn → one
  `messages.create` call. There is no second call to reuse a cached prefix.
- **Only** when a proposal is emitted in a separate turn from `log_activity` is it
  2 calls — the minority path.

So the within-analysis caching benefit is essentially absent. That collapses
caching's value to cross-*call* reuse, which for us is:

- **Production cron:** ~zero. One call per analysis, and runs are minutes-to-hours
  apart — past the ~5-minute cache TTL.
- **A sync run with several new activities:** small, occasional.
- **Eval / benchmark loop:** the only real reuse — many back-to-back analyses with
  an identical system prompt, and especially the **fixed judge** system prompt
  reused across every case. Even here the savings are modest at our volume
  (mostly a little dev-loop latency).

## Decision

**Do not add prompt caching.** For this application's actual call pattern
(single-call analyses, spread-out cron runs, low volume) the ROI is low, and it
would add `cache_control` plumbing to the provider seam (`llm.py`) for negligible
production benefit.

We also decline the related "reorder the user message volatile-last" change: its
justification here would be model *attention*, not caching, and with evals already
at 98–100% the expected gain is marginal while it would churn `PROMPT_VERSION` and
force a re-eval.

## Consequences

- Every analysis call reprocesses the full prompt at standard input price. At a
  few analyses per day this is immaterial.
- The provider seam stays simple (plain `system` string + message strings).
- The ordering knowledge is documented, so this is a *considered* skip, not an
  oversight.

## Revisit this if…

- The coach starts making genuinely multi-turn tool loops (many calls per
  analysis) — then within-analysis prefix reuse becomes real.
- Volume rises a lot (many athletes / high frequency), making input-token cost
  material.
- The eval/benchmark loop becomes a cost or latency bottleneck in development —
  caching the fixed judge prefix would be the first, most targeted thing to add.

## The general lesson

Profile the real call pattern — count the calls, compare the reuse distance to the
cache TTL — *before* applying "order by volatility" as a reflex. Caching and
volatility-ordering are real and load-bearing in high-throughput or genuinely
multi-turn systems (chatbots, deep agent loops, RAG over large stable documents).
This workflow simply isn't shaped like that.
