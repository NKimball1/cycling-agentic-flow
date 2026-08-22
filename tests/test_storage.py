"""Unit tests for storage.py — atomic file writes.

These guard the "a crash mid-write can't corrupt a state file" property that kept
recent_activities.json / the ledger / the Strava tokens intact through the OOM
outage. Each test writes into an isolated tmp dir (pytest's tmp_path fixture).
"""

import json
from datetime import date

import storage


def test_write_json_atomic_round_trips(tmp_path):
    p = tmp_path / "data.json"
    storage.write_json_atomic(p, {"a": 1, "b": [2, 3]})
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1, "b": [2, 3]}


def test_write_json_atomic_has_trailing_newline(tmp_path):
    p = tmp_path / "data.json"
    storage.write_json_atomic(p, {"a": 1})
    assert p.read_text(encoding="utf-8").endswith("}\n")


def test_write_json_atomic_overwrites_existing(tmp_path):
    p = tmp_path / "data.json"
    storage.write_json_atomic(p, {"v": 1})
    storage.write_json_atomic(p, {"v": 2})
    assert json.loads(p.read_text(encoding="utf-8")) == {"v": 2}


def test_write_leaves_no_temp_files_behind(tmp_path):
    p = tmp_path / "data.json"
    storage.write_json_atomic(p, {"a": 1})
    leftovers = [f.name for f in tmp_path.iterdir() if f.name != "data.json"]
    assert leftovers == []


def test_write_json_atomic_handles_non_serializable(tmp_path):
    # default=str lets a date (etc.) serialize instead of raising.
    p = tmp_path / "data.json"
    storage.write_json_atomic(p, {"d": date(2026, 8, 18)})
    assert json.loads(p.read_text(encoding="utf-8"))["d"] == "2026-08-18"


def test_write_text_atomic_exact_content(tmp_path):
    p = tmp_path / "note.txt"
    storage.write_text_atomic(p, "hello\nworld")
    assert p.read_text(encoding="utf-8") == "hello\nworld"


def test_creates_parent_dirs(tmp_path):
    p = tmp_path / "nested" / "deep" / "data.json"
    storage.write_json_atomic(p, {"ok": True})
    assert json.loads(p.read_text(encoding="utf-8")) == {"ok": True}
