"""Plan-adjustment ledger — the propose -> approve -> apply gate.

This is where the agent stops being just an analyst and becomes an actor: when
the evidence warrants it, the coach PROPOSES a change to the upcoming plan (via
the propose_plan_adjustment tool in coach.py). The proposal lands here as a
`pending` record — it does NOT touch training_plan.json. A human reviews it with
this module's CLI and approves or rejects. Only APPROVED adjustments feed back
into future analyses, so nothing changes the athlete's plan without a yes.

Why a separate ledger instead of editing the plan:
  - the plan is a stable weekly TEMPLATE; adjustments are dated, one-off
  - every proposal stays auditable (who/what/why/when, and its status)
  - approve / reject / revert is just a status flip, never a destructive edit

Record shape:
  {
    "id": "adj-20260814-1800-a1b2",
    "created": "2026-08-14T18:00:00",
    "for_date": "2026-08-20",          # date/session the change applies to
    "type": "reduce_intensity",         # reduce_intensity|move_session|add_rest|...
    "impact": "tier-0",                 # tier-0 small/reversible, tier-1 structural
    "summary": "Swap Thursday VO2 for easy Z2.",
    "rationale": "Form -34 heading into Thursday; ...",
    "status": "pending"                 # pending|approved|rejected
  }

CLI:
  python plan_adjustments.py                 # list all (pending first)
  python plan_adjustments.py --approve <id>
  python plan_adjustments.py --reject <id>
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime

import config

_ACTIVE = ("pending", "approved")  # statuses that still "count" for dedupe/context


def load() -> list:
    path = config.PLAN_ADJUSTMENTS_PATH
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _save(items: list) -> None:
    path = config.PLAN_ADJUSTMENTS_PATH
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(items, indent=2) + "\n", encoding="utf-8")


def _make_id(fields: dict, created: str) -> str:
    digest = hashlib.sha256(
        f"{fields.get('for_date')}|{fields.get('type')}|{fields.get('summary')}".encode("utf-8")
    ).hexdigest()[:4]
    return f"adj-{created[:10].replace('-', '')}-{created[11:16].replace(':', '')}-{digest}"


def propose(fields: dict) -> dict:
    """Append a PENDING proposal. Deduplicates: if a pending/approved adjustment
    already exists for the same (for_date, type), it is returned unchanged rather
    than piling up duplicates on every re-analysis. Returns a small status dict."""
    items = load()
    for_date = (fields.get("for_date") or "")[:10]
    kind = fields.get("type") or "note"
    for existing in items:
        if (
            existing.get("status") in _ACTIVE
            and (existing.get("for_date") or "")[:10] == for_date
            and existing.get("type") == kind
        ):
            return {"status": "duplicate", "id": existing["id"], "detail": "a matching proposal already exists"}

    created = datetime.now().isoformat(timespec="seconds")
    record = {
        "id": _make_id(fields, created),
        "created": created,
        "for_date": for_date,
        "type": kind,
        "impact": fields.get("impact") or "tier-0",
        "summary": fields.get("summary") or "",
        "rationale": fields.get("rationale") or "",
        "status": "pending",
    }
    items.append(record)
    _save(items)
    return {"status": "proposed", "id": record["id"], "detail": "pending athlete approval"}


def get(adj_id: str) -> dict | None:
    """Fetch one adjustment record by id, or None."""
    for a in load():
        if a.get("id") == adj_id:
            return a
    return None


def annotate(adj_id: str, **fields) -> bool:
    """Merge extra fields into a record (e.g. the Discord message id the bot
    posted it as). Returns True if found. Keeps delivery bookkeeping in the
    ledger so it survives a bot restart."""
    items = load()
    for a in items:
        if a.get("id") == adj_id:
            a.update(fields)
            _save(items)
            return True
    return False


def set_status(adj_id: str, status: str) -> bool:
    """Approve/reject a proposal by id. Returns True if found."""
    items = load()
    for item in items:
        if item.get("id") == adj_id:
            item["status"] = status
            item["decided"] = datetime.now().isoformat(timespec="seconds")
            _save(items)
            return True
    return False


def pending() -> list:
    return [a for a in load() if a.get("status") == "pending"]


def for_context(as_of_date: str | None) -> list:
    """Adjustments relevant when analyzing an activity on `as_of_date`: everything
    still active (pending or approved) whose target date is that day or later, so
    the coach honors approved changes and doesn't re-propose pending ones."""
    cutoff = (as_of_date or "")[:10]
    out = []
    for a in load():
        if a.get("status") not in _ACTIVE:
            continue
        if not cutoff or (a.get("for_date") or "")[:10] >= cutoff:
            out.append(a)
    return out


def _print_list(items: list) -> None:
    if not items:
        print("No plan adjustments on record.")
        return
    order = {"pending": 0, "approved": 1, "rejected": 2}
    for a in sorted(items, key=lambda x: (order.get(x.get("status"), 9), x.get("for_date", ""))):
        print(f"[{a.get('status','?').upper():<8}] {a.get('id')}  for {a.get('for_date','?')}  ({a.get('impact','?')})")
        print(f"    {a.get('type','?')}: {a.get('summary','')}")
        print(f"    why: {a.get('rationale','')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Review and decide on proposed plan adjustments.")
    parser.add_argument("--approve", metavar="ID", help="Approve a proposal by id.")
    parser.add_argument("--reject", metavar="ID", help="Reject a proposal by id.")
    args = parser.parse_args()

    if args.approve or args.reject:
        adj_id = args.approve or args.reject
        status = "approved" if args.approve else "rejected"
        if set_status(adj_id, status):
            print(f"{adj_id} -> {status}.")
        else:
            print(f"No adjustment with id {adj_id}.")
        return

    _print_list(load())
    n = len(pending())
    if n:
        print(f"\n{n} pending. Approve with:  python plan_adjustments.py --approve <id>")


if __name__ == "__main__":
    main()
