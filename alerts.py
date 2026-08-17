"""Failure alerting — make silent breakage loud.

An unattended run that dies only writes to cron.log, which nobody watches. This
pings you when a run fails: a Discord DM (primary — reuses the approver bot's
token, sent via Discord's REST API so even a short-lived cron process can DM you)
with email as a fallback.

It DE-DUPES so a persistent failure doesn't spam you: the same failure alerts
ONCE, then stays quiet until the failure changes or clears. State is a tiny
git-ignored file. `alert_once(None, ...)` on a clean run re-arms it, so the next
genuine break alerts again.
"""

from __future__ import annotations

import json

import config
import net
import storage

_DISCORD_API = "https://discord.com/api/v10"
_STATE_PATH = config.PROJECT_ROOT / ".alert_state.json"


def _discord_dm(content: str) -> bool:
    """DM the approver via Discord's REST API. Returns True if sent, False if
    Discord isn't configured. Raises on a real send failure (so send() can fall
    back to email)."""
    if not (config.DISCORD_BOT_TOKEN and config.DISCORD_APPROVER_ID):
        return False
    session = net.retrying_session(total=2)
    headers = {"Authorization": f"Bot {config.DISCORD_BOT_TOKEN}"}
    # Open (or fetch) the DM channel with the approver, then post to it.
    ch = session.post(
        f"{_DISCORD_API}/users/@me/channels",
        headers=headers,
        json={"recipient_id": str(config.DISCORD_APPROVER_ID)},
        timeout=15,
    )
    ch.raise_for_status()
    channel_id = ch.json()["id"]
    msg = session.post(
        f"{_DISCORD_API}/channels/{channel_id}/messages",
        headers=headers,
        json={"content": content[:1900]},  # Discord's hard limit is 2000 chars
        timeout=15,
    )
    msg.raise_for_status()
    return True


def _email(subject: str, body: str) -> bool:
    """Send the alert as an email if the email channel is configured."""
    if "email" not in config.NOTIFY_CHANNELS:
        return False
    import notify  # lazy: avoids importing the delivery stack unless we alert

    notify.send_email(f"[cycling-agent] {subject}", body)
    return True


def send(subject: str, body: str) -> None:
    """Best-effort alert: Discord DM first, email fallback. Never raises — an
    alert failing must not crash the run it's trying to report on."""
    try:
        if _discord_dm(f"⚠️ **{subject}**\n{body}"):
            return
    except Exception as exc:
        print(f"  (discord alert failed, trying email: {exc})")
    try:
        _email(subject, body)
    except Exception as exc:
        print(f"  (email alert failed: {exc})")


def _load_signature() -> str | None:
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8")).get("signature")
    except Exception:
        return None


def alert_once(signature: str | None, subject: str = "", body: str = "") -> None:
    """Alert only when `signature` differs from the last one we alerted on, so a
    failure that repeats every run pings you just once. Pass signature=None to
    clear the state after a clean run (re-arming future alerts)."""
    last = _load_signature()
    if signature is None:
        if last is not None:
            storage.write_json_atomic(_STATE_PATH, {"signature": None})
        return
    if signature == last:
        return  # same failure as last time — already told you
    send(subject, body)
    storage.write_json_atomic(_STATE_PATH, {"signature": signature})
