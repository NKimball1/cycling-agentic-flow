"""Unit tests for net.py — the retry/resilience helpers.

The whole point of net.py is "retry transient failures, fail fast on permanent
ones." These tests pin exactly that contract so a refactor can't quietly turn a
permanent error into a slow retry loop, or drop the retries that keep the cron
job alive through an API blip.
"""

import pytest
import requests

import net


def test_call_with_retries_returns_on_first_success():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "ok"

    assert net.call_with_retries(fn, base_delay=0) == "ok"
    assert calls["n"] == 1  # no needless retries on success


def test_call_with_retries_retries_transient_then_succeeds():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("transient")
        return "ok"

    assert net.call_with_retries(fn, attempts=4, base_delay=0) == "ok"
    assert calls["n"] == 3


def test_call_with_retries_raises_after_exhausting_attempts():
    def fn():
        raise ConnectionError("always down")

    with pytest.raises(ConnectionError):
        net.call_with_retries(fn, attempts=3, base_delay=0)


def test_call_with_retries_does_not_retry_unlisted_exception():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise ValueError("permanent")

    # ValueError isn't in retry_on, so it must surface immediately.
    with pytest.raises(ValueError):
        net.call_with_retries(fn, attempts=5, base_delay=0, retry_on=(ConnectionError,))
    assert calls["n"] == 1


def test_retrying_session_mounts_adapters_with_retry_config():
    session = net.retrying_session(total=7)
    assert isinstance(session, requests.Session)
    for scheme in ("https://", "http://"):
        assert scheme in session.adapters
    assert session.adapters["https://"].max_retries.total == 7
