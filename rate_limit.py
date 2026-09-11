"""
Rate limiting logic for the currency conversion service.

This module tracks requests per API key hash and enforces per‑minute
and per‑day limits. The numeric limits are passed in from the main
application based on the key's tier or subscription plan.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, List

from fastapi import HTTPException

# In‑memory store of request timestamps per key. It is per process: it resets
# on restart and is not shared between workers, so run a single worker.
request_timestamps: Dict[str, List[float]] = {}

# Guards read-modify-write of ``request_timestamps`` across handler threads.
_LOCK = threading.Lock()


def allow_request(
    key_hash: str,
    per_minute: int,
    per_day: int,
) -> None:
    """Enforce rate limits for a given key (thread-safe wrapper)."""
    with _LOCK:
        _allow_request_locked(key_hash, per_minute, per_day)


def _allow_request_locked(
    key_hash: str,
    per_minute: int,
    per_day: int,
) -> None:
    """Enforce rate limits for a given key.

    Args:
        key_hash: The hash of the API key making the request.
        per_minute: Number of allowed requests in the last 60 seconds.
        per_day: Number of allowed requests in the last 24 hours.

    Raises:
        HTTPException: With status 429 if either limit is exceeded.
    """
    now = time.time()
    if key_hash not in request_timestamps:
        request_timestamps[key_hash] = []
    timestamps = [t for t in request_timestamps[key_hash] if now - t < 86400]
    last_minute_count = len([t for t in timestamps if now - t < 60])
    if per_minute is not None and last_minute_count >= per_minute:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded (per minute). Please slow down.",
        )
    if per_day is not None and len(timestamps) >= per_day:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded (per day). Please try again later.",
        )
    timestamps.append(now)
    request_timestamps[key_hash] = timestamps