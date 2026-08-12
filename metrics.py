"""Shared unit conversions and training-load math.

Used by BOTH data sources — the .fit parser (fit_parser.py) and the Strava
mapper (strava.py) — so a given activity yields the same load numbers no matter
where its metrics came from. This is the shared contract that lets us swap the
source without touching the analysis.
"""

from __future__ import annotations

from typing import Optional

METERS_PER_MILE = 1609.344
FEET_PER_METER = 3.280839895
MPH_PER_MPS = 2.236936292


def m_to_mi(meters: Optional[float]) -> Optional[float]:
    return round(meters / METERS_PER_MILE, 2) if meters is not None else None


def m_to_ft(meters: Optional[float]) -> Optional[int]:
    return round(meters * FEET_PER_METER) if meters is not None else None


def mps_to_mph(mps: Optional[float]) -> Optional[float]:
    return round(mps * MPH_PER_MPS, 1) if mps is not None else None


def c_to_f(celsius: Optional[float]) -> Optional[int]:
    return round(celsius * 9 / 5 + 32) if celsius is not None else None


def pace_min_per_mi(mps: Optional[float]) -> Optional[float]:
    """Average pace in minutes per mile — the natural unit for runs and walks."""
    if not mps or mps <= 0:
        return None
    return round(METERS_PER_MILE / mps / 60, 2)


def normalized_power(power_samples: list[int]) -> Optional[int]:
    """Normalized Power (NP): a physiologically weighted average power.

    Algorithm (Coggan): 30-second rolling average of power, each value raised to
    the 4th power, averaged, then take the 4th root. This weights hard surges
    more heavily than a plain average. Needs at least 30 seconds of ~1Hz samples.

    (The Strava path doesn't need this — Strava reports NP directly as
    `weighted_average_watts` — but the .fit path computes it from raw samples.)
    """
    if len(power_samples) < 30:
        return None

    rolling = []
    window_sum = sum(power_samples[:30])
    rolling.append(window_sum / 30)
    for i in range(30, len(power_samples)):
        window_sum += power_samples[i] - power_samples[i - 30]
        rolling.append(window_sum / 30)

    fourth_power_mean = sum(p ** 4 for p in rolling) / len(rolling)
    return round(fourth_power_mean ** 0.25)


def power_tss(duration_sec: float, np_watts: int, ftp: int) -> Optional[float]:
    """Power-based Training Stress Score (cycling): overall load of the ride.

    TSS = (seconds * NP * IF) / (FTP * 3600) * 100, where IF = NP / FTP.
    An hour at exactly FTP = 100 TSS by definition.
    """
    if not (duration_sec and np_watts and ftp):
        return None
    intensity_factor = np_watts / ftp
    return round((duration_sec * np_watts * intensity_factor) / (ftp * 3600) * 100, 1)


def hr_tss(
    duration_sec: float,
    avg_hr: int,
    resting_hr: int,
    threshold_hr: int,
) -> Optional[float]:
    """Heart-rate-based training load, comparable across any sport.

    Mirrors the power-TSS shape but uses an HR "intensity factor" from heart
    rate reserve (Karvonen), which subtracts the resting-HR floor so easy
    efforts aren't overstated:

        hr_IF   = (avg_HR - resting_HR) / (threshold_HR - resting_HR)
        hr_TSS  = hours * hr_IF^2 * 100

    One hour at threshold HR = 100, matching the power-TSS convention. This is
    an estimate — HR lags effort and drifts with heat/fatigue — but it gives a
    single load number for rides, runs, and walks alike.
    """
    if not (duration_sec and avg_hr and resting_hr and threshold_hr):
        return None
    if threshold_hr <= resting_hr:
        return None
    hr_if = (avg_hr - resting_hr) / (threshold_hr - resting_hr)
    hr_if = max(0.0, hr_if)  # guard against sub-resting averages
    return round((duration_sec / 3600) * hr_if ** 2 * 100, 1)
