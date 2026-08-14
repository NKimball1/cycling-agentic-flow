# Project Writeup — Agentic Training-Analysis Workflow

*Context for a job-hunting assistant: this summarizes a personal software project and the concrete skills it demonstrates. It's written to be lifted into resume bullets, cover letters, or interview talking points.*

## One-line summary

Built and deployed an agentic LLM workflow (Python + Anthropic API) that automatically pulls my cycling/running/walking activities from Strava, analyzes each one against a structured training plan using Claude with tool calling, emails me a coaching breakdown, and grades its own output quality with an LLM-as-judge evaluation harness — running unattended on a cloud VM via cron.

## What the system does, end to end

1. **Detects a new activity** — polls the Strava API on a schedule, dedupes against already-processed IDs.
2. **Normalizes the data** — maps Strava's activity JSON into a shared, source-agnostic "metrics" contract (the same contract a local `.fit`-file parser produces, so either data source is interchangeable).
3. **Computes training load in code** — cross-sport Training Stress Score (TSS): power-based (Normalized Power vs FTP) when a power meter is present, heart-rate-based (Karvonen HR-reserve) otherwise, and a flagged duration-based estimate when there's neither. Every load value carries a `load_source` tag so nothing is silently guessed.
4. **Runs the analysis with an LLM** — Claude receives the metrics, the training plan, and recent history, then produces a structured Markdown coaching report (plan-vs-actual, cross-sport fatigue, target verdict, next-days notes) and calls a `log_activity` tool to write the activity into history.
5. **Delivers it** — emails the report via SMTP.
6. **Logs and evaluates** — every run is recorded to SQLite (tokens, latency, model, prompt version); a separate eval harness scores analysis quality against a rubric using a second, independent LLM as judge.

## The most recent batch of changes (what I just shipped)

- **Code-computed, flagged load estimates.** For activities with no power and no HR, the system now computes a rough TSS from duration and labels it `load_source: "estimated"`. The prompt instructs the model to use the number but explicitly call it an estimate and never fabricate its own load figure. Design principle: *let deterministic code do math; let the model do judgment.*
- **Fairer evaluation judge.** The LLM judge was penalizing correct output as "unverifiable" because it lacked context the coach model had. Fixed by feeding the judge the same recent history and code-computed weekday, so it grades grounding accurately instead of flagging real data.
- **Model benchmark harness (`--compare`).** Runs the full eval case set across multiple Claude models (Haiku / Sonnet / Opus) with the *judge model held fixed*, then reports quality × token cost × latency in one table — turning "which model should I use?" into a measured decision rather than a guess.

## Engineering concepts demonstrated (interview-ready)

- **Agentic LLM design with tool use.** A manual tool-call loop where the model orchestrates a side-effecting write (`log_activity`) through a typed, validated boundary — not just a one-shot prompt. I can articulate *when a tool earns its place* (selective retrieval or a controlled write) vs. when plain code is the right call.
- **Deterministic-vs-judgment separation.** Parsing, unit conversion, date arithmetic, and TSS math live in code (reliable, testable); only interpretation is delegated to the model. This directly reduced a class of LLM errors (e.g., wrong weekdays, malformed pace strings).
- **Provider-agnostic abstraction (the "seam" pattern).** All LLM access goes through an `LLMClient` interface with neutral tool specs; adding OpenAI/Gemini/a local model later means writing one adapter with zero changes to business logic. Same swap pattern used for the data source (`.fit` file vs. Strava API behind one metrics contract).
- **Eval-driven development / LLM observability.** Prompt versioning via content hash, a SQLite run log capturing tokens/latency/prompt-version per run, and an LLM-as-judge scoring rubric. I understand the pitfalls firsthand: judge independence (don't grade with the rubric you handed the model — Goodhart's Law), and eval noise (I measured ~5-point score swings on identical configs, so I know small deltas are meaningless without more cases and averaged runs).
- **OAuth integration & token lifecycle.** Strava OAuth with `activity:read_all` scope and refresh-token rotation, plus the operational consequence I designed around: rotating refresh tokens mean a single "home" for auth (the server), so the client is never run in parallel.
- **Cloud deployment & automation.** Deployed to an AWS Lightsail flat-rate VM, scheduled via cron, git-based deploy (push local → pull server), with a billing-alert safety setup — a deliberate choice of predictable flat-rate billing over metered.
- **Security & privacy hygiene.** The training plan contains sensitive medical information; I git-ignored the real data, committed sanitized `*.example` templates, and scrubbed git history so the repo is safe to make public.

## Tech stack

Python 3.12 · Anthropic Claude API (Messages API, tool use) · Strava REST API + OAuth · SQLite · SMTP email · AWS Lightsail + cron · git-based deployment

## Suggested resume bullets (pick/trim)

- Designed and deployed an agentic LLM workflow (Python, Anthropic API with tool calling) that ingests activity data from the Strava API, analyzes it against a structured plan, and delivers automated coaching reports — running unattended on a cloud VM via cron.
- Built an eval/observability layer (SQLite run log + LLM-as-judge scoring + prompt versioning) that makes prompt and model changes measurable, and a benchmark comparing model quality against token cost and latency.
- Architected a provider-agnostic LLM abstraction and a shared data-source contract, so swapping the model vendor or the data input requires a single adapter and no business-logic changes.
- Separated deterministic computation (training-load math, date handling, parsing) from model judgment to eliminate a class of LLM factual errors, and enforced privacy hygiene (git-ignored medical data, scrubbed history, sanitized public templates).
