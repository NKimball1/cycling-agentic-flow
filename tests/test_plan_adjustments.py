"""Unit tests for plan_adjustments.py — the propose -> approve ledger.

Covers the dedup rule, status transitions, and the context filter that decides
what the coach sees (pending/approved/rejected, future-only). Each test points
the ledger at an isolated temp file so the real data/plan_adjustments.json is
never touched.
"""

import pytest

import config
import plan_adjustments as pa


@pytest.fixture(autouse=True)
def temp_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PLAN_ADJUSTMENTS_PATH", tmp_path / "adjustments.json")


def _p(for_date, type_="reduce_intensity"):
    return {"for_date": for_date, "type": type_, "summary": "s", "rationale": "r"}


def test_propose_creates_a_pending_record():
    r = pa.propose(_p("2026-08-25"))
    assert r["status"] == "proposed"
    items = pa.load()
    assert len(items) == 1
    assert items[0]["status"] == "pending"
    assert items[0]["for_date"] == "2026-08-25"


def test_propose_dedupes_same_date_and_type():
    a = pa.propose(_p("2026-08-25"))
    b = pa.propose({**_p("2026-08-25"), "summary": "worded differently"})
    assert b["status"] == "duplicate"
    assert b["id"] == a["id"]
    assert len(pa.load()) == 1


def test_propose_different_type_is_not_a_duplicate():
    pa.propose(_p("2026-08-25", "reduce_intensity"))
    r = pa.propose(_p("2026-08-25", "add_rest"))
    assert r["status"] == "proposed"
    assert len(pa.load()) == 2


def test_set_status_and_get():
    r = pa.propose(_p("2026-08-25"))
    assert pa.set_status(r["id"], "approved") is True
    assert pa.get(r["id"])["status"] == "approved"
    assert pa.set_status("does-not-exist", "approved") is False


def test_rejected_does_not_block_a_fresh_proposal():
    # Dedup is pending/approved-only, so a re-warranted idea can still be proposed.
    r = pa.propose(_p("2026-08-25"))
    pa.set_status(r["id"], "rejected")
    r2 = pa.propose(_p("2026-08-25"))
    assert r2["status"] == "proposed"
    assert r2["id"] != r["id"]


def test_pending_returns_only_pending():
    p = pa.propose(_p("2026-08-25"))
    a = pa.propose(_p("2026-08-26"))
    pa.set_status(a["id"], "approved")
    pending = pa.pending()
    assert [x["id"] for x in pending] == [p["id"]]


def test_for_context_future_only_all_active_statuses():
    pa.propose(_p("2026-08-25", "reduce_intensity"))          # pending, future
    ap = pa.propose(_p("2026-08-26", "add_rest"))
    pa.set_status(ap["id"], "approved")                       # approved, future
    rj = pa.propose(_p("2026-08-27", "swap_sessions"))
    pa.set_status(rj["id"], "rejected")                       # rejected, future -> still shown
    pa.propose(_p("2026-08-01", "extend_recovery"))           # pending, PAST -> dropped

    dates = {a["for_date"] for a in pa.for_context("2026-08-20")}
    assert dates == {"2026-08-25", "2026-08-26", "2026-08-27"}


def test_annotate_merges_fields():
    r = pa.propose(_p("2026-08-25"))
    assert pa.annotate(r["id"], discord_message_id=123) is True
    assert pa.get(r["id"])["discord_message_id"] == 123
    assert pa.annotate("does-not-exist", x=1) is False
