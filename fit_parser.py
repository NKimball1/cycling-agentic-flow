"""Parse a local .fit file into structured activity metrics (any sport).

`get_activity_metrics()` reads a .fit file you exported from Strava — ride, run,
or walk — and returns a JSON-serializable dict of metrics in imperial units.
Every field degrades gracefully to None if the underlying data isn't present.

Two kinds of training load are reported so activities are comparable across
sports:
  - power_tss : cycling-only, from Normalized Power vs FTP.
  - hr_tss    : any sport with heart rate, from HR vs threshold/resting HR.
  - load_tss  : the unified number to compare on — power_tss for rides with a
                meter, else hr_tss.

Run it standalone to test (no API key needed):
    python fit_parser.py data/ride.fit 253 39 163   # path ftp resting_hr threshold_hr
"""

from __future__ import annotations

import json
import sys
from typing import Optional

from fitparse import FitFile

# Shared unit conversions + load math live in metrics.py so the .fit path and
# the Strava path produce identical numbers. Aliased to the names used below.
from metrics import (
    m_to_mi as _m_to_mi,
    m_to_ft as _m_to_ft,
    mps_to_mph as _mps_to_mph,
    c_to_f as _c_to_f,
    pace_min_per_mi as _pace_min_per_mi,
    pace_str as _pace_str,
    normalized_power as _normalized_power,
    power_tss as _power_tss,
    hr_tss as _hr_tss,
)


# --- .fit parsing ----------------------------------------------------------
def _session_field(fitfile: FitFile, name: str):
    """Return a field value from the .fit 'session' summary message, or None.

    Garmin/Strava files include a 'session' message with device-computed
    summaries (total distance, avg power, etc.). We prefer these when present
    and fall back to computing from the raw per-second records otherwise.
    """
    for session in fitfile.get_messages("session"):
        value = session.get_value(name)
        if value is not None:
            return value
    return None


def get_activity_metrics(
    fit_path: str,
    ftp: Optional[int] = None,
    resting_hr: Optional[int] = None,
    threshold_hr: Optional[int] = None,
) -> dict:
    """Parse a .fit file and return structured activity metrics (imperial units).

    Args:
        fit_path: Path to a local .fit file (ride, run, or walk).
        ftp: Functional Threshold Power in watts. Required to derive power-based
            NP / IF / power_tss (cycling only).
        resting_hr: Resting heart rate (bpm). Required, with threshold_hr, for
            the cross-sport hr_tss load estimate.
        threshold_hr: Threshold heart rate (bpm).

    Returns:
        A JSON-serializable dict of metrics. Missing metrics are None.
    """
    fitfile = FitFile(fit_path)

    # Sport type. NP/IF/power_tss are cycling concepts anchored to a cycling
    # FTP, so we compute them only for rides (a run's "power" comes from a
    # footpod on a different scale). Heart-rate load (hr_tss), by contrast, is
    # computed for every sport so a ride, run, and walk are comparable.
    sport = None
    sub_sport = None
    for msg in fitfile.get_messages("sport"):
        sport = msg.get_value("sport") or sport
        sub_sport = msg.get_value("sub_sport") or sub_sport
    if sport is None:
        sport = _session_field(fitfile, "sport")
        sub_sport = sub_sport or _session_field(fitfile, "sub_sport")
    # Treat unknown/untagged files as cycling (this is a cycling tool).
    is_cycling = sport in (None, "cycling")

    # Collect per-second record series. .fit "record" messages are the
    # timestamped samples logged (typically) once per second.
    power_samples: list[int] = []
    hr_samples: list[int] = []
    cadence_samples: list[int] = []
    altitudes: list[float] = []
    start_time = None
    end_time = None

    for record in fitfile.get_messages("record"):
        values = {d.name: d.value for d in record}

        ts = values.get("timestamp")
        if ts is not None:
            start_time = start_time or ts
            end_time = ts

        if values.get("power") is not None:
            power_samples.append(int(values["power"]))
        if values.get("heart_rate") is not None:
            hr_samples.append(int(values["heart_rate"]))
        if values.get("cadence") is not None:
            cadence_samples.append(int(values["cadence"]))

        # Prefer enhanced_altitude (higher precision) when available.
        alt = values.get("enhanced_altitude", values.get("altitude"))
        if alt is not None:
            altitudes.append(float(alt))

    # --- Duration (seconds) ---
    moving_sec = _session_field(fitfile, "total_timer_time")
    elapsed_sec = _session_field(fitfile, "total_elapsed_time")
    if elapsed_sec is None and start_time and end_time:
        elapsed_sec = (end_time - start_time).total_seconds()
    if moving_sec is None:
        moving_sec = elapsed_sec

    # --- Distance / speed ---
    distance_m = _session_field(fitfile, "total_distance")
    avg_speed_mps = _session_field(fitfile, "avg_speed") or _session_field(
        fitfile, "enhanced_avg_speed"
    )
    max_speed_mps = _session_field(fitfile, "max_speed") or _session_field(
        fitfile, "enhanced_max_speed"
    )

    # --- Power ---
    avg_power = _session_field(fitfile, "avg_power")
    if avg_power is None and power_samples:
        avg_power = round(sum(power_samples) / len(power_samples))
    max_power = _session_field(fitfile, "max_power")
    if max_power is None and power_samples:
        max_power = max(power_samples)

    # Normalized Power / IF / TSS — cycling only (see is_cycling above).
    np_watts = None
    if is_cycling:
        np_watts = _session_field(fitfile, "normalized_power")
        if np_watts is None:
            np_watts = _normalized_power(power_samples)

    intensity_factor = round(np_watts / ftp, 2) if (np_watts and ftp) else None
    power_tss = _power_tss(moving_sec, np_watts, ftp) if (np_watts and ftp) else None

    # Total mechanical work in kilojoules (roughly = kcal for cycling).
    work_kj = round(sum(power_samples) / 1000) if power_samples else None

    # --- Heart rate ---
    avg_hr = _session_field(fitfile, "avg_heart_rate")
    if avg_hr is None and hr_samples:
        avg_hr = round(sum(hr_samples) / len(hr_samples))
    max_hr = _session_field(fitfile, "max_heart_rate")
    if max_hr is None and hr_samples:
        max_hr = max(hr_samples)

    # HR-based load works for any sport; the unified load_tss prefers the
    # power number for rides and falls back to HR otherwise.
    hr_tss = _hr_tss(moving_sec, avg_hr, resting_hr, threshold_hr)
    load_tss = power_tss if power_tss is not None else hr_tss

    # Pace — the natural intensity unit for runs and walks. Provided both as a
    # clock string (avg_pace, for display) and decimal minutes (for math).
    avg_pace = _pace_str(avg_speed_mps)
    avg_pace_min_per_mi = _pace_min_per_mi(avg_speed_mps)

    # --- Cadence ---
    avg_cadence = _session_field(fitfile, "avg_cadence")
    if avg_cadence is None and cadence_samples:
        # Ignore zeros (coasting) so the average reflects actual pedaling.
        pedaling = [c for c in cadence_samples if c > 0]
        avg_cadence = round(sum(pedaling) / len(pedaling)) if pedaling else None

    # --- Elevation gain: sum of positive altitude deltas ---
    elevation_gain_m = _session_field(fitfile, "total_ascent")
    if elevation_gain_m is None and len(altitudes) > 1:
        elevation_gain_m = sum(
            max(0.0, altitudes[i] - altitudes[i - 1]) for i in range(1, len(altitudes))
        )

    # --- Temperature ---
    avg_temp_c = _session_field(fitfile, "avg_temperature")

    return {
        "source": "fit",
        "date": start_time.isoformat() if start_time else None,
        "sport": sport,
        "sub_sport": sub_sport,
        "moving_duration_min": round(moving_sec / 60, 1) if moving_sec else None,
        "elapsed_duration_min": round(elapsed_sec / 60, 1) if elapsed_sec else None,
        "distance_mi": _m_to_mi(distance_m),
        "avg_speed_mph": _mps_to_mph(avg_speed_mps),
        "max_speed_mph": _mps_to_mph(max_speed_mps),
        "avg_pace": avg_pace,
        "avg_pace_min_per_mi": avg_pace_min_per_mi,
        "avg_power_w": avg_power,
        "max_power_w": max_power,
        "normalized_power_w": np_watts,
        "intensity_factor": intensity_factor,
        "power_tss": power_tss,
        "hr_tss": hr_tss,
        "load_tss": load_tss,
        "work_kj": work_kj,
        "avg_hr_bpm": avg_hr,
        "max_hr_bpm": max_hr,
        "avg_cadence_rpm": avg_cadence,
        "elevation_gain_ft": _m_to_ft(elevation_gain_m),
        "avg_temp_f": _c_to_f(avg_temp_c),
        "ftp_used_w": ftp,
        "resting_hr_used": resting_hr,
        "threshold_hr_used": threshold_hr,
        "has_power": bool(power_samples),
        "has_heart_rate": bool(hr_samples),
    }


if __name__ == "__main__":
    # Quick standalone test:  python fit_parser.py <path> [ftp] [resting_hr] [threshold_hr]
    path = sys.argv[1] if len(sys.argv) > 1 else "data/ride.fit"
    ftp_arg = int(sys.argv[2]) if len(sys.argv) > 2 else None
    resting_arg = int(sys.argv[3]) if len(sys.argv) > 3 else None
    threshold_arg = int(sys.argv[4]) if len(sys.argv) > 4 else None
    metrics = get_activity_metrics(
        path, ftp=ftp_arg, resting_hr=resting_arg, threshold_hr=threshold_arg
    )
    print(json.dumps(metrics, indent=2, default=str))
