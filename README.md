# Cycling Agentic Flow

[![tests](https://github.com/NKimball1/cycling-agentic-flow/actions/workflows/tests.yml/badge.svg)](https://github.com/NKimball1/cycling-agentic-flow/actions/workflows/tests.yml)

A workflow that analyzes your training activities — rides, runs, and walks —
against your training plan. Plain Python gathers the activity's metrics, your
plan, and your recent history; Claude does the parts that actually need judgment:
reading those numbers in context, writing a coach's analysis, and recording the
activity back into your history.

Two ways in — a local `.fit` file (`analyze.py`) or an automatic Strava pull
(`sync.py`) — both feed the same analysis core, and the finished write-up can be
emailed to you.

## How it's built (deterministic gather, a judgment step, a gated action)

The reflex with LLM projects is to reach for tools and orchestration everywhere.
The better instinct is the **simplest tier that does each job**:

- **Single call** — you already have the context and want one structured answer.
- **Workflow** — several steps, but *your code* controls the sequence.
- **Agent** — the task is open-ended and the *model* must decide the steps.

Gathering the inputs is deterministic: we *always* need the activity, the plan,
and the history, so that's plain Python ([`fit_parser.py`](fit_parser.py),
[`plan_loader.py`](plan_loader.py)) — no decision to hand the model. The analysis
is the judgment step, so it goes to Claude ([`coach.py`](coach.py)).

```
fit_parser + plan_loader ── gather ──▶ coach.py ──▶ analysis
   (deterministic Python)              (Claude: judgment)
                                          │
                                          ├─▶ log_activity             (always: writes history)
                                          └─▶ propose_plan_adjustment  (only when warranted; gated by approval)
```

**The two tools, and why each earns its place.** A tool earns its place for one of
three reasons; two apply here (the third doesn't — yet):

1. **A side-effecting write** — persisting the analyzed activity to your history.
   The model doesn't decide *whether* to log (it always does); the `log_activity`
   tool gives that write a typed, validated boundary while *our* code performs the
   actual file write.
2. **A gated action the model chooses to take** — when the data clearly warrants it
   (e.g. deep fatigue before a quality day), the coach calls
   `propose_plan_adjustment` to *propose* a change to the upcoming plan. This is the
   genuinely agentic step: the model decides *whether* to act. It never edits the
   plan directly — it writes a pending proposal you approve (in Discord, or via
   CLI), and only approved changes feed back into future analyses.
3. **Selective retrieval** — the model deciding *what* to fetch. Not here yet: we
   always fetch the same three things, so gathering stays deterministic. It becomes
   justified only once the history is a large live feed and "find me comparable
   rides" is a real choice.

So the tool-use loop is minimal and justified — a validated write boundary plus one
gated, model-decided action — not ceremony.

**Two swappable seams, same pattern.** The codebase is agnostic on two axes, each
behind a shared contract:

- **Data source** — `.fit` (`fit_parser.py`) or Strava (`strava.py`) both produce
  the same metrics dict, so the analysis doesn't care where data came from.
- **LLM provider** — `coach.py` talks to an `LLMClient` interface (`llm.py`), not a
  vendor SDK. Only the Claude adapter (`ClaudeClient`) exists today, but adding
  another is "write an adapter," not surgery. Pick it via `LLM_PROVIDER` in `.env`.

The rest of the app (plan, logging, delivery) is untouched by either swap.

## Project layout

```
config.py               # loads .env: FTP, HR anchors, model, Strava, email
metrics.py              # shared unit + load math (used by both data sources)
fit_parser.py           # get_activity_metrics(): .fit -> metrics (any sport)
strava.py               # Strava OAuth + fetch + map API JSON -> same metrics
strava_auth.py          # one-time Strava authorization (run once)
plan_loader.py          # read plan + activities; append_activity() writes history
llm.py                  # provider seam: LLMClient interface + ClaudeClient adapter
coach.py                # prompt + log_activity tool + flow; provider- & source-agnostic
notify.py               # Phase 3 delivery (email); toggled by NOTIFY_CHANNELS
analyze.py              # entry point A: analyze a local .fit
sync.py                 # entry point B: pull new Strava activities, analyze, deliver
data/
  training_plan.json    # your plan (edit this)
  recent_activities.json# your activity history, all sports (edited + auto-appended)
  ride.fit              # drop a .fit here for analyze.py
output/                 # saved analyses (git-ignored)
```

## Setup

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows PowerShell
# source .venv/bin/activate    # macOS/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure your secrets and profile
copy .env.example .env         # Windows
# cp .env.example .env          # macOS/Linux
# then edit .env:
#   required : ANTHROPIC_API_KEY, FTP_WATTS, RESTING_HR, THRESHOLD_HR
#   Phase 2  : STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET  (Strava sync)
#   Phase 3  : NOTIFY_CHANNELS, GMAIL_ADDRESS, GMAIL_APP_PASSWORD  (email delivery)

# 4. Add your training plan and history (git-ignored — stays private)
copy data\training_plan.example.json data\training_plan.json
copy data\recent_activities.example.json data\recent_activities.json
# then edit those two with your real plan and recent activities
```

> **Privacy:** `.env`, `data/training_plan.json`, `data/recent_activities.json`,
> `output/`, `.strava_tokens.json`, and `*.fit` are all git-ignored — your keys,
> health details, and training data never get committed. Only the `*.example`
> templates are tracked.

## Run it

```bash
# Drop the .fit to analyze at data/ride.fit, then:
python analyze.py --fit data/ride.fit
```

You'll see what was gathered, then the analysis, saved to
`output/analysis_<timestamp>.md`. The analyzed activity is also appended to
`data/recent_activities.json` so the next run has it as context.

## Automatic Strava sync (Phase 2)

Connect Strava once, then pull and analyze new activities on demand. Note: the
Strava API doesn't serve `.fit` files — it serves the computed activity summary,
which `strava.py` maps into the same metrics shape the coach already expects.

```bash
python strava_auth.py            # one-time: authorize (browser), stores a refresh token
python strava.py                 # smoke test: list your latest activities
python sync.py --mark-synced     # baseline: treat current activities as already handled
python sync.py                   # analyze any NEW activities (logs + saves + delivers)
python sync.py --dry-run --limit 1   # analyze the newest, write/send nothing (cheap test)
```

Each sync costs 1 Strava read to list, +1 per new activity — trivial against the
1,000/day limit, so it's safe to run often (manually, or on a schedule via
Windows Task Scheduler).

## Email delivery (Phase 3)

Set `NOTIFY_CHANNELS=email` plus `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` (a Google
**App Password**, not your login) in `.env`, and every finished analysis is
emailed to you as formatted HTML. Delivery is off until those are set.

```bash
python notify.py                 # send yourself a test email
```

Delivery is modular (`notify.py`): adding SMS/push/Telegram later is just another
sender plus a name in `NOTIFY_CHANNELS`.

## Test the pieces independently (no API key needed)

```bash
python fit_parser.py data/ride.fit 253 39 163   # path, FTP, resting_hr, threshold_hr
python plan_loader.py                            # print plan + recent activities
```

## What the coach reports

- **Activity Summary** — plain-language recap
- **Plan vs. Actual** — did it hit the prescribed target?
- **Trends & Cross-Sport Load** — patterns across all sports; how running/walking
  load affects cycling recovery
- **Did You Hit Your Targets?** — a clear verdict
- **Coach's Notes** — concrete next steps

## Metrics (from either source)

Sport type, duration (moving + elapsed), distance, avg/max speed, **pace**
(min/mi), avg/max power, **Normalized Power**, **Intensity Factor**, work (kJ),
avg/max heart rate, avg cadence, elevation gain, temperature — plus three load
numbers so sports are comparable:

- **`power_tss`** — cycling load from Normalized Power ÷ FTP (rides with a meter).
- **`hr_tss`** — heart-rate load, computed for *any* sport from HR reserve vs your
  threshold/resting HR (an estimate — HR lags effort and drifts with heat).
- **`load_tss`** — the unified number to compare on: `power_tss` for rides with a
  meter, else `hr_tss`.

Anything unavailable comes back as `null` and the coach works with what's there.

## Roadmap

**Original phases — all shipped:**

- **Phase 1:** manual trigger, local `.fit`, multi-sport analysis, auto-log. ✅
- **Phase 2:** Strava OAuth, new-activity detection, map API JSON to the shared
  metrics contract, `sync.py`. ✅
- **Phase 3:** email delivery of the finished analysis (`notify.py`). ✅

**Deployed & running unattended** on an AWS Lightsail VM via cron (5-minute poll). ✅

**Beyond the phases — built since:**

- **LLM-agnostic seam** — all model access behind an `LLMClient` interface; a new
  provider is one adapter, no business-logic changes. ✅
- **Rolling training-load state** — Fitness / Fatigue / Form (CTL/ATL/TSB) derived
  from history each run and injected as context. ✅
- **Weather enrichment** — historical conditions for the ride (Open-Meteo, no key),
  used proportionately. ✅
- **Eval & observability** — a SQLite run log, an LLM-as-judge rubric, prompt
  versioning, a deterministic propose-or-not check, and a model benchmark
  (`--compare`) scoring quality × cost × latency. ✅
- **Plan adjustments (the agent acts)** — the coach *proposes* plan changes when
  the data warrants; you approve them, gated by a human. ✅
- **Interactive approval** — a Discord bot DMs proposals with Approve / Reject
  buttons (`discord_bot.py`, runs as a systemd service). ✅
- **Production hardening** — retry-with-backoff on transient failures, per-activity
  isolation, atomic state-file writes, and de-duped failure alerts. ✅
- **Tests & CI** — a `pytest` suite run on every push via GitHub Actions. ✅

**Ideas, not yet built:**

- More notification channels (SMS / push).
- Tier-1 (structural) plan adjustments, beyond the current small/reversible ones.
- More eval cases, especially "should-propose" scenarios.
- Move the adjustments ledger to SQLite (removes a small read/write race).
- Lightweight memory/health monitoring on the VM.

## Design decisions & operations

Longer-form engineering notes live in [`docs/`](docs/):

- [**ADR 0001 — Prompt caching & context ordering**](docs/decisions/0001-prompt-caching-and-ordering.md)
  — how the prompt is ordered, and why prompt caching was considered and
  deliberately skipped for this workload (the tool loop almost never loops, so
  the reuse that makes caching pay off isn't there).
- [**ADR 0002 — Derive rolling load state from history**](docs/decisions/0002-derive-load-state-vs-store.md)
  — why Fitness/Fatigue/Form is recomputed from history each run instead of stored
  as a running counter (idempotent, self-healing on data fixes, crash-safe).
- [**ADR 0003 — A separate adjustment ledger**](docs/decisions/0003-adjustment-ledger-vs-mutating-plan.md)
  — why the agent proposes into its own ledger instead of editing the plan, keeping
  the source of truth human-owned and every action auditable and reversible.
- [**ADR 0004 — Deterministic check vs. LLM judge**](docs/decisions/0004-deterministic-check-vs-llm-judge.md)
  — why the propose-or-not decision is graded in code (exact, noise-free) while
  subjective analysis quality goes to the LLM judge — matching the measurement to
  the property.
- [**ADR 0005 — A provider-agnostic LLM seam**](docs/decisions/0005-provider-agnostic-llm-seam.md)
  — why model access goes through an `LLMClient` interface (build the seam, not
  every adapter), so swapping vendors is one file and telemetry stays neutral.
- [**Incident 2026-08-20 — instance wedged (OOM)**](docs/incidents/2026-08-20-oom-wedge.md)
  — postmortem of the first production outage: root cause (no swap on a 512 MB
  box), recovery, and the swap fix. No data lost, thanks to idempotent syncing
  and atomic state-file writes.
