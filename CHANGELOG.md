# Changelog

Notable changes to this project. The format loosely follows
[Keep a Changelog](https://keepachangelog.com/). This is a personal, continuously
deployed project rather than one with tagged releases, so entries are grouped by
milestone (newest first).

## Reliability, testing & docs

### Added
- **Test suite** — 74 `pytest` unit tests across the math, load model, schema
  normalization, date helpers, atomic writes, retry logic, ledger, and alerts.
- **Continuous integration** — GitHub Actions runs the suite on every push (badge
  in the README).
- **Architecture Decision Records** — ADR 0001–0005 in `docs/decisions/` covering
  prompt caching, deriving-vs-storing state, the adjustment ledger, deterministic
  vs. LLM-judge measurement, and the provider-agnostic LLM seam.
- **Incident postmortem** — `docs/incidents/` documenting the first production
  outage and its fix.

### Fixed
- Production OOM outage on the 512 MB VM (no swap) — added a 2 GB swapfile.
- ID collision on identical plan proposals made within the same minute.

## Agentic action & interactive approval

### Added
- **Plan-adjustment propose → approve gate** — the coach proposes changes to the
  upcoming plan when warranted; a human approves, and only approved changes feed
  back into future analyses. The plan file is never edited directly.
- **Discord approver bot** — DMs proposals with Approve/Reject buttons; runs as a
  systemd service.
- **Production hardening** — retry-with-backoff on transient API failures,
  per-activity failure isolation, atomic state-file writes, and de-duped failure
  alerts (Discord DM with email fallback).

## Coaching intelligence

### Added
- **Rolling training-load state** — Fitness/Fatigue/Form (CTL/ATL/TSB) derived
  from history each run and injected as context.
- **Weather enrichment** — historical conditions for the ride (Open-Meteo), used
  proportionately.
- **Weekday calendar & inter-activity date facts** — computed in code so the model
  never derives a weekday or a gap between sessions itself.

### Changed
- **History normalized** to a single flat schema, enforced on every write so
  schema drift can't recur (with a one-time backfill script).

## Evaluation & observability

### Added
- **SQLite run log** capturing tokens, latency, model, and prompt version per run.
- **LLM-as-judge eval harness** scoring analyses against a rubric, with prompt
  versioning by content hash.
- **Model benchmark** (`--compare`) scoring quality × token cost × latency with the
  judge held fixed.
- **Deterministic proposal-decision check** — propose-or-not graded in code against
  a labeled expectation, separate from the subjective rubric.

## Core workflow (Phases 1–3)

### Added
- **Phase 1** — analyze a local `.fit` file against the training plan, multi-sport,
  with a `log_activity` tool that records the activity to history.
- **Phase 2** — Strava OAuth, new-activity detection, and mapping the API JSON into
  the same metrics contract the `.fit` parser produces.
- **Phase 3** — email delivery of the finished analysis.
- **Deployment** — running unattended on an AWS Lightsail VM via cron.
- **Provider-agnostic LLM seam** — all model access behind an `LLMClient` interface
  so swapping vendors is one adapter, not a rewrite.
