# Architecture Decision Records

Short notes on the decisions that shaped this project — what was chosen, what was
considered, and *why*. Each record is self-contained and dated; superseded ones
stay for history rather than being deleted.

| # | Decision | Status |
|---|----------|--------|
| [0001](0001-prompt-caching-and-ordering.md) | Prompt caching & context ordering — considered, deliberately skipped | Accepted |
| [0002](0002-derive-load-state-vs-store.md) | Derive rolling load state (CTL/ATL/TSB) from history instead of storing a counter | Accepted |
| [0003](0003-adjustment-ledger-vs-mutating-plan.md) | A separate adjustment ledger — the agent proposes, never edits the plan | Accepted |
| [0004](0004-deterministic-check-vs-llm-judge.md) | Grade the propose/not decision in code; keep the LLM judge for subjective quality | Accepted |
| [0005](0005-provider-agnostic-llm-seam.md) | A provider-agnostic LLM seam — talk to an interface, not a vendor SDK | Accepted |

Together these cover the system's spine: how context is built, how rolling state is
kept, how the agent takes action safely, and how its behavior is measured.

For operational history (outages, fixes), see [`../incidents/`](../incidents/).
