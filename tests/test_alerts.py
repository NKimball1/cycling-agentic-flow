"""Unit tests for alerts.py — de-duped failure notifications.

The value of alert_once is that a failure alerts ONCE and then stays quiet until
it changes or clears, so an ongoing outage doesn't spam you every cron run. These
tests pin that dedup/re-arm behavior against an isolated temp state file, with
the real delivery (send) stubbed so nothing hits the network.
"""

import pytest

import alerts
import config


@pytest.fixture(autouse=True)
def temp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(alerts, "_STATE_PATH", tmp_path / "alert_state.json")


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(alerts, "send", lambda subject, body: calls.append(subject))
    return calls


def test_discord_dm_returns_false_when_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_BOT_TOKEN", None)
    monkeypatch.setattr(config, "DISCORD_APPROVER_ID", None)
    assert alerts._discord_dm("hi") is False  # no config -> no network attempt


def test_email_returns_false_when_channel_disabled(monkeypatch):
    monkeypatch.setattr(config, "NOTIFY_CHANNELS", [])
    assert alerts._email("subj", "body") is False


def test_alert_once_sends_on_new_signature(sent):
    alerts.alert_once("sigA", "subj", "body")
    assert sent == ["subj"]


def test_alert_once_dedupes_repeat_signature(sent):
    alerts.alert_once("sigA", "subj", "body")
    alerts.alert_once("sigA", "subj", "body")  # same failure -> no second alert
    assert sent == ["subj"]


def test_alert_once_sends_on_changed_signature(sent):
    alerts.alert_once("sigA", "a", "body")
    alerts.alert_once("sigB", "b", "body")
    assert sent == ["a", "b"]


def test_alert_once_clear_rearms(sent):
    alerts.alert_once("sigA", "a", "body")
    alerts.alert_once(None)                  # clean run clears the state
    alerts.alert_once("sigA", "a2", "body")  # same signature alerts again
    assert sent == ["a", "a2"]


def test_alert_once_clear_with_no_state_is_noop(sent):
    alerts.alert_once(None)
    assert sent == []


def test_alert_once_namespaces_by_key(sent):
    # Same signature under different keys alerts independently...
    alerts.alert_once("s", "run failure", "b", key="run")
    alerts.alert_once("s", "delivery failure", "b", key="delivery")
    assert sent == ["run failure", "delivery failure"]
    # ...but a repeat under the same key is still de-duped...
    alerts.alert_once("s", "run again", "b", key="run")
    assert sent == ["run failure", "delivery failure"]
    # ...and clearing one key leaves the other's dedup intact.
    alerts.alert_once(None, key="run")
    alerts.alert_once("s", "delivery repeat", "b", key="delivery")
    assert sent == ["run failure", "delivery failure"]
