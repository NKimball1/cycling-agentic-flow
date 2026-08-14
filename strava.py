"""Strava API client: OAuth token management + activity fetching.

Phase 2 swaps the *source* of activity data from a local .fit file to the
Strava API. The downstream contract (a metrics dict handed to coach.py) stays
the same, so only this module is new — the analysis core is untouched.

Note: the Strava API does NOT serve the original .fit file. It serves already-
computed activity summaries (distance, moving time, weighted_average_watts =
Normalized Power, average HR, elevation, ...) plus optional per-second streams.
Increment 2 will map that JSON into our metrics shape; this increment is just
auth + fetching so we can confirm the connection works.

OAuth model (personal, single-user):
  1. One-time: open authorize_url(), approve, exchange the returned code for
     tokens (see strava_auth.py). We store the refresh token.
  2. Every run after: get_access_token() refreshes the short-lived access token
     using the stored refresh token — no browser step ever again.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

import config
import metrics

AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
API_BASE = "https://www.strava.com/api/v3"

# Callback domain in your Strava app is "localhost", so any localhost redirect
# is accepted. Nothing needs to listen here — we read the code from the URL.
REDIRECT_URI = "http://localhost/exchange_token"
# read_all so private activities are visible too.
SCOPE = "activity:read_all"


def _require_credentials() -> None:
    if not config.STRAVA_CLIENT_ID or not config.STRAVA_CLIENT_SECRET:
        raise RuntimeError(
            "STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET are not set. Add them to .env "
            "from your app at https://www.strava.com/settings/api."
        )


# --- Token storage ---------------------------------------------------------
def _save_tokens(tokens: dict) -> None:
    Path(config.STRAVA_TOKENS_PATH).write_text(json.dumps(tokens, indent=2), encoding="utf-8")


def _load_tokens() -> dict | None:
    path = Path(config.STRAVA_TOKENS_PATH)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# --- OAuth -----------------------------------------------------------------
def authorize_url() -> str:
    """The URL you open once in a browser to approve access."""
    _require_credentials()
    return (
        f"{AUTHORIZE_URL}?client_id={config.STRAVA_CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}&response_type=code"
        f"&scope={SCOPE}&approval_prompt=auto"
    )


def exchange_code(code: str) -> dict:
    """One-time: trade the browser authorization code for tokens, and store them."""
    _require_credentials()
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": config.STRAVA_CLIENT_ID,
            "client_secret": config.STRAVA_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    resp.raise_for_status()
    tokens = resp.json()
    _save_tokens(tokens)
    return tokens


def get_access_token() -> str:
    """Return a valid access token, refreshing it via the stored refresh token."""
    _require_credentials()
    tokens = _load_tokens()
    if tokens is None:
        raise RuntimeError("No Strava tokens found. Run:  python strava_auth.py")

    # Access tokens last ~6 hours; refresh a minute early to be safe.
    if time.time() >= tokens.get("expires_at", 0) - 60:
        resp = requests.post(
            TOKEN_URL,
            data={
                "client_id": config.STRAVA_CLIENT_ID,
                "client_secret": config.STRAVA_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
            },
            timeout=30,
        )
        resp.raise_for_status()
        refreshed = resp.json()
        # Strava may rotate the refresh token — always persist what it returns.
        tokens.update(refreshed)
        _save_tokens(tokens)

    return tokens["access_token"]


# --- API calls -------------------------------------------------------------
def _get(path: str, params: dict | None = None):
    resp = requests.get(
        f"{API_BASE}{path}",
        headers={"Authorization": f"Bearer {get_access_token()}"},
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def list_recent_activities(per_page: int = 10, page: int = 1) -> list:
    """Most recent activities (summary objects), newest first."""
    return _get("/athlete/activities", {"per_page": per_page, "page": page})


def get_activity(activity_id: int) -> dict:
    """Full detail for one activity (more fields than the summary list)."""
    return _get(f"/activities/{activity_id}")


# --- Mapping Strava JSON -> our metrics contract ---------------------------
# Strava reports the computed summary directly, so instead of parsing a .fit we
# map its fields into the SAME dict shape fit_parser.get_activity_metrics()
# produces, and reuse the shared load math (metrics.py). Same contract, so
# coach.py doesn't change.

_CYCLING_TYPES = {
    "Ride", "MountainBikeRide", "GravelRide", "VirtualRide",
    "EBikeRide", "EMountainBikeRide", "Velomobile",
}
_RUNNING_TYPES = {"Run", "TrailRun", "VirtualRun"}


def _normalize_sport(sport_type: str | None) -> str | None:
    if sport_type in _CYCLING_TYPES:
        return "cycling"
    if sport_type in _RUNNING_TYPES:
        return "running"
    if sport_type == "Walk":
        return "walking"
    if sport_type == "Hike":
        return "hiking"
    return sport_type.lower() if sport_type else None


def _round_or_none(x):
    return round(x) if x is not None else None


def activity_to_metrics(
    detail: dict,
    ftp: int | None = None,
    resting_hr: int | None = None,
    threshold_hr: int | None = None,
) -> dict:
    """Map a Strava activity detail JSON into our standard metrics dict."""
    sport_type = detail.get("sport_type") or detail.get("type")
    sport = _normalize_sport(sport_type)
    is_cycling = sport == "cycling"

    moving_sec = detail.get("moving_time")
    elapsed_sec = detail.get("elapsed_time")

    avg_power = _round_or_none(detail.get("average_watts"))
    max_power = detail.get("max_watts")
    # Strava's weighted_average_watts IS Normalized Power (rides with power).
    np_watts = detail.get("weighted_average_watts") if is_cycling else None

    intensity_factor = round(np_watts / ftp, 2) if (np_watts and ftp) else None
    power_load = metrics.power_tss(moving_sec, np_watts, ftp) if (np_watts and ftp) else None

    avg_hr = _round_or_none(detail.get("average_heartrate"))
    max_hr = _round_or_none(detail.get("max_heartrate"))
    hr_load = metrics.hr_tss(moving_sec, avg_hr, resting_hr, threshold_hr)
    load_tss = power_load if power_load is not None else hr_load

    return {
        "source": "strava",
        "strava_activity_id": detail.get("id"),
        "date": detail.get("start_date_local"),
        "sport": sport,
        "sub_sport": sport_type,
        "moving_duration_min": round(moving_sec / 60, 1) if moving_sec else None,
        "elapsed_duration_min": round(elapsed_sec / 60, 1) if elapsed_sec else None,
        "distance_mi": metrics.m_to_mi(detail.get("distance")),
        "avg_speed_mph": metrics.mps_to_mph(detail.get("average_speed")),
        "max_speed_mph": metrics.mps_to_mph(detail.get("max_speed")),
        "avg_pace": metrics.pace_str(detail.get("average_speed")),
        "avg_pace_min_per_mi": metrics.pace_min_per_mi(detail.get("average_speed")),
        "avg_power_w": avg_power,
        "max_power_w": max_power,
        "normalized_power_w": np_watts,
        "intensity_factor": intensity_factor,
        "power_tss": power_load,
        "hr_tss": hr_load,
        "load_tss": load_tss,
        "work_kj": _round_or_none(detail.get("kilojoules")),
        "avg_hr_bpm": avg_hr,
        "max_hr_bpm": max_hr,
        "avg_cadence_rpm": _round_or_none(detail.get("average_cadence")),
        "elevation_gain_ft": metrics.m_to_ft(detail.get("total_elevation_gain")),
        "avg_temp_f": metrics.c_to_f(detail.get("average_temp")),
        "ftp_used_w": ftp,
        "resting_hr_used": resting_hr,
        "threshold_hr_used": threshold_hr,
        "has_power": detail.get("average_watts") is not None,
        "has_heart_rate": bool(detail.get("has_heartrate")) or avg_hr is not None,
    }


def get_activity_metrics(
    activity_id: int,
    ftp: int | None = None,
    resting_hr: int | None = None,
    threshold_hr: int | None = None,
) -> dict:
    """Fetch one activity from Strava and return it as a metrics dict."""
    detail = get_activity(activity_id)
    return activity_to_metrics(
        detail, ftp=ftp, resting_hr=resting_hr, threshold_hr=threshold_hr
    )


if __name__ == "__main__":
    # No arg: list recent activities.  With an id: print that activity's metrics.
    #   python strava.py
    #   python strava.py 19573661186
    if len(sys.argv) > 1:
        m = get_activity_metrics(
            int(sys.argv[1]),
            ftp=config.FTP_WATTS,
            resting_hr=config.RESTING_HR,
            threshold_hr=config.THRESHOLD_HR,
        )
        print(json.dumps(m, indent=2))
    else:
        for a in list_recent_activities(per_page=10):
            print(
                f"{a['start_date_local'][:10]}  {a['sport_type']:<12} "
                f"{a['name'][:40]:<40}  id={a['id']}"
            )
