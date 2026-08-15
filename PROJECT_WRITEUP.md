# Project Writeup — Agentic Training-Analysis Workflow

*Context for a job-hunting assistant: this summarizes a personal software project and the concrete skills it demonstrates. It's written to be lifted into resume bullets, cover letters, or interview talking points.*

## One-line summary

Built and deployed an agentic LLM system (Python + Anthropic API) that pulls my cycling/running/walking activities from Strava, tracks rolling fitness/fatigue/form, analyzes each activity against a structured training plan using Claude with tool calling, and — when the data warrants — **proposes plan adjustments that I approve from my phone via a Discord bot**. It emails a coaching breakdown, records every run to SQLite, and grades its own quality with an LLM-as-judge eval harness plus a deterministic behavioral check — running unattended on a cloud VM via cron.

## What the system does, end to end

1. **Detects a new activity** — polls the Strava API on a schedule, dedupes against already-processed IDs.
2. **Normalizes the data** — maps Strava's activity JSON into a shared, source-agnostic "metrics" contract (the same contract a local `.fit`-file parser produces, so either data source is interchangeable).
3. **Computes training load in code** — cross-sport Training Stress Score (TSS): power-based (Normalized Power vs FTP), heart-rate-based (Karvonen HR-reserve) otherwise, or a flagged duration estimate when there's neither. Every value carries a `load_source` tag so nothing is silently guessed.
4. **Tracks rolling load state** — derives CTL/ATL/TSB (Fitness / Fatigue / Form) from the activity history on every run, and enriches with historical weather for the ride's time and place. These are computed in code and injected as facts.
5. **Runs the analysis with an LLM** — Claude receives the metrics, plan, history, and the computed facts, then produces a structured Markdown coaching report and calls a `log_activity` tool to persist the activity.
6. **Proposes plan adjustments (acts)** — when the evidence clearly warrants it (e.g. deeply negative form before a quality day), the coach calls a *conditional* `propose_plan_adjustment` tool that writes a **pending** proposal to a ledger — it never edits the plan directly.
7. **Human-in-the-loop approval** — a Discord bot DMs me the proposal with Approve/Reject buttons; a tap flips the ledger. Only *approved* adjustments feed back into future analyses, closing the loop from "suggests" to "adapts."
8. **Delivers** — emails the report via SMTP (with an approval footer when a proposal is pending).
9. **Logs and evaluates** — every run is recorded to SQLite (tokens, latency, model, prompt version); an eval harness scores analysis quality with an independent LLM judge **and** deterministically checks whether the propose-or-not decision matched a labeled expectation.

## Latest work (this iteration)

- **The agent now acts, gated by a human.** Added a propose→approve→apply capability: a *conditional* tool the model calls only when warranted, a separate auditable ledger (never mutating the source plan), and a feedback loop where approved changes shape later analyses. This is the leap from an agent that *analyzes* to one that *acts* with a safety gate.
- **Interactive approval via a Discord bot.** A persistent bot DMs proposals with Approve/Reject buttons and calls a single `set_status` function on tap. Because the approval gate was one clean function, the whole interactive front-end was a *thin adapter, not a rewrite*. Runs as a systemd service.
- **Rolling training-load state (CTL/ATL/TSB).** Fitness/Fatigue/Form derived from history every run — *recomputed from the source of truth rather than stored*, so it's idempotent and self-healing. Surfaced (and I then fixed) a real data-quality bug where missing load data produced a confidently wrong analysis.
- **Weather enrichment.** Fetches historical weather (Open-Meteo, no key) for the ride and injects it as facts, with an eval criterion that rewards *proportionate* use — flag genuine extremes, don't over-attribute a benign day.
- **Data normalization at the write boundary.** Unified schema drift from hand-curated history into one flat schema, enforced on every write so it can't recur, with a one-time backfill script (backup + dry-run + apply).
- **Deterministic proposal evaluation.** Propose-or-not is a *behavioral* property, so it's checked in code against a labeled expectation — noise-free and Goodhart-resistant — rather than by the LLM judge. Catches both over- and under-proposing.
- **Model benchmark → root-caused fix.** A `--compare` harness scores Haiku/Sonnet/Opus with the judge held fixed; a per-criterion drill-down traced a cheaper model's weakness to self-computed dates, which I fixed with an injected weekday calendar (any date becomes a lookup) — a model-agnostic improvement found *through* the benchmark.

## Engineering concepts demonstrated (interview-ready)

- **Human-in-the-loop agentic action.** A model that proposes a side-effecting change, persisted as pending, applied only after human approval — the propose→approve→apply pattern, with the "should I even act?" decision itself being a model judgment (a *conditional* tool, unlike an always-called one).
- **Agentic LLM design with tool use.** A manual tool-call loop with a typed, validated write boundary; terminal vs. non-terminal tools (the model can propose, get the result, then log). I can articulate *when a tool earns its place* vs. when plain code is right.
- **Deterministic-vs-judgment separation.** Parsing, dates, TSS/CTL-ATL-TSB math, weather, and the propose/not-propose *check* live in code; only interpretation and the propose *decision* go to the model. This eliminated whole classes of LLM error (wrong weekdays, malformed numbers).
- **Eval-driven development with the right tool for each signal.** LLM-as-judge for nuanced quality (with judge independence — Goodhart's Law — and measured ~5-pt run-to-run noise), *plus* a deterministic behavioral check for propose-or-not. Prompt versioning by content hash; a SQLite run log for observability; a fixed-judge model benchmark for cost/quality/latency tradeoffs.
- **Derive-from-source-of-truth over stored state.** Rolling load is recomputed each run rather than accumulated, making it idempotent and self-healing — and normalization is enforced at the write boundary so bad data can't re-enter.
- **Provider-agnostic abstraction (the "seam" pattern).** LLM access goes through an `LLMClient` interface with neutral tool specs; a new vendor is one adapter, zero business-logic changes. Same swap pattern for the data source (`.fit` vs. Strava behind one metrics contract).
- **OAuth integration & token lifecycle.** Strava OAuth (`activity:read_all`, refresh-token rotation) and the operational consequence I designed around (single auth "home").
- **Cloud deployment & operations.** AWS Lightsail flat-rate VM, cron scheduling, git-based deploy, a persistent systemd service for the bot, and real ops scars: Python venv isolation under PEP 668, and a blank-env-var parsing bug found in deployment.
- **Security & privacy hygiene.** Training data contains sensitive medical info; git-ignored real data, committed sanitized `*.example` templates, scrubbed history, and an auth gate on the state-changing approval action (bot locked to my user ID).

## Tech stack

Python 3.12 · Anthropic Claude API (Messages API, tool use) · Strava REST API + OAuth · Open-Meteo API · discord.py (gateway bot, interactions/buttons) · SQLite · SMTP email · AWS Lightsail + cron + systemd · git-based deployment

## Suggested resume bullets (pick/trim)

- Designed and deployed an agentic LLM system (Python, Anthropic API with tool calling) that analyzes training data and **proposes plan changes gated by human-in-the-loop approval** — the model decides *whether* to act, writes a pending proposal to an auditable ledger, and I approve via a Discord bot; approved changes feed back into future runs.
- Built an eval layer that uses **the right measurement for each signal** — an LLM-as-judge rubric for nuanced quality plus a *deterministic* behavioral check for the agent's action decisions — with prompt versioning, a SQLite observability log, and a fixed-judge model benchmark (quality × cost × latency).
- Separated deterministic computation (training-load/CTL-ATL-TSB math, dates, weather, action checks) from model judgment, eliminating classes of LLM factual error; used the eval/observability layer to root-cause a model weakness into a model-agnostic prompt fix.
- Architected a provider-agnostic LLM abstraction and a shared data-source contract (swap vendor or input with one adapter); derived rolling state from the source of truth for idempotency and enforced schema normalization at the write boundary.
- Deployed and operated an unattended system on AWS Lightsail (cron + systemd), handling real production concerns: OAuth token rotation, Python environment isolation (PEP 668/venv), and privacy hygiene for sensitive medical data.
