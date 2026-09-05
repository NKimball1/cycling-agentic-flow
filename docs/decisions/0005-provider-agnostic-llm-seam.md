# ADR 0005 — A provider-agnostic LLM seam (interface + adapter)

- **Status:** Accepted
- **Date:** 2026-08-13
- **Context area:** architecture, portability, testability

## Context

The analysis step needs a large language model. The path of least resistance is
to import the Anthropic SDK directly in `coach.py` and call it inline. The
question was whether to couple the business logic to one vendor's SDK, or put a
boundary between them.

## Decision

Introduce a **provider seam**: `coach.py` (and everything else) talks to an
`LLMClient` interface defined in `llm.py`, never to a vendor SDK. Tools are
described **neutrally** (`{name, description, parameters}`); each adapter
translates that into its provider's function-calling dialect. `ClaudeClient` is
the only adapter today, selected via `LLM_PROVIDER` in `.env`.

Crucially, we built the *seam*, not a pile of adapters — YAGNI. Only Claude is
implemented; the value is the boundary being in place.

## Why

- **Swappability without surgery.** Adding OpenAI / Gemini / a local model is
  "write one adapter," with zero changes to `coach.py`, `sync.py`, the eval
  harness, or anything else. The blast radius of a vendor change is one file.
- **Vendor specifics stay in one place.** The tool-call dialect, the response
  shape, the tool-use loop mechanics — all live behind the interface, so the rest
  of the code reads in domain terms, not SDK terms.
- **Neutral telemetry.** Calls return an `LLMResult` (text + token counts + model)
  rather than a vendor response object, so observability (the run log, the eval
  harness) never leaks a provider type into the business logic.
- **Testability.** Business logic that depends on an interface can be exercised
  without the real SDK — the tests never construct a live client.

## Consequences

- A thin translation layer exists in each adapter (neutral tool spec ->
  Anthropic's `input_schema`, etc.). That translation — not text generation — is
  the real work in any future adapter.
- The same seam pattern is used a second time for the **data source**: `.fit`
  (`fit_parser.py`) and Strava (`strava.py`) both produce the same metrics dict,
  so the analysis doesn't care where data came from. Two swappable axes, one
  pattern: define a contract, implement per backend, choose via config.
- One adapter today means the abstraction is unproven against a second provider —
  accepted, because the interface is small and the cost of the seam is low.

## Revisit this if…

- A second provider is actually needed — then the interface gets its first real
  test, and any leaky assumptions surface (and get fixed) at that point.
- A provider needs a capability the neutral interface can't express (streaming,
  provider-specific tool features) — extend the interface deliberately rather than
  reaching around it back to a raw SDK.

## The general lesson

Put the abstraction at the **seam where change is likely** (the model vendor), and
give it the **simplest interface that covers today's needs**. Build the boundary,
not every implementation behind it — the seam pays for itself in clarity and
testability even before a second backend ever exists.
