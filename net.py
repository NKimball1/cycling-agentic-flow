"""Network resilience — retry transient failures, fail fast on permanent ones.

An unattended cron job lives or dies on how it treats the network. The key
distinction is transient vs. permanent:

  - TRANSIENT (an overloaded API, a dropped connection, a 429/503): retry with
    exponential backoff — the next attempt will probably succeed.
  - PERMANENT (bad auth, a 400, a 404): fail fast and loudly — retrying can't
    fix it, and burning retries just delays a real error you need to see.

This module centralizes both patterns so every outbound call gets the same
tested treatment instead of each caller hand-rolling its own.
"""

from __future__ import annotations

import time
from typing import Callable

import requests
from requests.adapters import HTTPAdapter

try:  # location moved across urllib3 versions
    from urllib3.util.retry import Retry
except ImportError:  # pragma: no cover
    from requests.packages.urllib3.util.retry import Retry


def retrying_session(total: int = 4, backoff_factor: float = 0.5) -> requests.Session:
    """A requests.Session that retries transient HTTP failures automatically.

    Retries connection errors and 429/5xx with exponential backoff
    (backoff_factor * 2**n seconds) and honors a Retry-After header. It does NOT
    retry 4xx like 400/401/403 — those are permanent, so they surface to the
    caller's raise_for_status() immediately instead of being masked by retries.
    """
    retry = Retry(
        total=total,
        connect=total,
        read=total,
        status=total,
        backoff_factor=backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
        respect_retry_after_header=True,
        raise_on_status=False,  # let the caller's raise_for_status() report the final error
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def call_with_retries(
    fn: Callable,
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    label: str = "",
    verbose: bool = False,
):
    """Call `fn()` with exponential backoff, retrying only `retry_on` exceptions.

    For non-HTTP calls (e.g. SMTP) where a Session adapter doesn't apply. Anything
    not in `retry_on`, and the final failed attempt, propagates immediately — so
    permanent errors (bad credentials) aren't retried into a long delay.
    """
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except retry_on as exc:
            if attempt == attempts:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            if verbose:
                print(f"  ({label or 'call'} failed: {exc}; retry {attempt}/{attempts - 1} in {delay:.0f}s)")
            time.sleep(delay)
