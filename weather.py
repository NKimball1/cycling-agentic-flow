"""Weather enrichment via Open-Meteo (no API key required).

Why this is plain code, not a tool the model calls: weather at a given place and
time is a *fact* to look up, not a judgment to make. So we fetch it here and hand
it to the coach as context — exactly like the load math and the date facts. The
model interprets conditions (heat stress, wind, humidity affecting decoupling);
it never guesses or fabricates the weather.

Source: Open-Meteo's forecast endpoint serves recent past days (comfortably
within range for an activity analyzed soon after it happened) and needs no key.
We request the activity's date at its start location, then read the hour that
matches its start time. Units are requested in imperial to match the rest of the
app (°F, mph, inches).

Best-effort by design: no GPS (indoor ride), a missing date, a network error, or
an API hiccup all return None and the analysis proceeds without weather. Weather
is enrichment — it must never break an analysis.
"""

from __future__ import annotations

import requests

import net

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Retries transient blips; the whole fetch is still wrapped best-effort below, so
# a persistent failure just means "no weather", never a crashed analysis.
_SESSION = net.retrying_session()

# The hourly fields we pull. apparent_temperature is the "feels like" that folds
# in humidity + wind — the number that actually predicts heat/cold stress.
_HOURLY = (
    "temperature_2m,apparent_temperature,relative_humidity_2m,"
    "precipitation,wind_speed_10m,weather_code"
)

# WMO weather interpretation codes -> short human labels (the useful subset).
_WMO = {
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Violent showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm w/ hail", 99: "Thunderstorm w/ hail",
}


def _at(hourly: dict, key: str, idx: int):
    """Value of an hourly series at `idx`, or None if absent."""
    arr = hourly.get(key) or []
    return arr[idx] if 0 <= idx < len(arr) else None


def get_weather(lat, lon, start_local_iso, timeout: int = 10) -> dict | None:
    """Weather at (lat, lon) for the hour of `start_local_iso` ('YYYY-MM-DDTHH...').

    Returns a compact dict of conditions, or None on any failure (no location,
    bad date, network/API error, or a date outside the endpoint's window).
    """
    if lat is None or lon is None or not start_local_iso:
        return None
    date = start_local_iso[:10]
    try:
        hour = int(start_local_iso[11:13])
    except (ValueError, TypeError):
        hour = 12  # no usable time -> midday is a reasonable default

    try:
        resp = _SESSION.get(
            FORECAST_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "start_date": date,
                "end_date": date,
                "hourly": _HOURLY,
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "precipitation_unit": "inch",
                "timezone": "auto",  # so returned hours are LOCAL, matching `hour`
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        hourly = resp.json().get("hourly", {})
        times = hourly.get("time", [])
        if not times:
            return None
        # Pick the hourly sample closest to the activity's start hour.
        idx = min(range(len(times)), key=lambda i: abs(int(times[i][11:13]) - hour))
        code = _at(hourly, "weather_code", idx)
        return {
            "temp_f": _at(hourly, "temperature_2m", idx),
            "apparent_temp_f": _at(hourly, "apparent_temperature", idx),
            "humidity_pct": _at(hourly, "relative_humidity_2m", idx),
            "wind_mph": _at(hourly, "wind_speed_10m", idx),
            "precip_in": _at(hourly, "precipitation", idx),
            "conditions": _WMO.get(code) if code is not None else None,
            "source": "open-meteo",
        }
    except Exception:
        # Any failure -> no weather, analysis continues. (Enrichment, not core.)
        return None


def enrich_metrics(metrics_dict: dict, timeout: int = 10) -> dict:
    """Attach a 'weather' block to a metrics dict when location + time allow.

    Reads `start_latlng` ([lat, lon]) and `date` from the dict. Mutates and
    returns the same dict; leaves it untouched if weather can't be resolved.
    """
    if metrics_dict.get("weather"):
        return metrics_dict  # already enriched
    latlng = metrics_dict.get("start_latlng") or []
    lat = latlng[0] if len(latlng) >= 2 else None
    lon = latlng[1] if len(latlng) >= 2 else None
    weather = get_weather(lat, lon, metrics_dict.get("date"), timeout=timeout)
    if weather:
        metrics_dict["weather"] = weather
    return metrics_dict


if __name__ == "__main__":
    # Quick manual check (Central Park-ish, noon):
    #   python weather.py
    import json

    print(json.dumps(get_weather(40.785, -73.968, "2026-08-14T12:00:00"), indent=2))
