"""
Rate limiting logic for the currency conversion service.

This module tracks requests per bucket (an API key hash, or a route and
client address for unauthenticated routes) and enforces per‑minute and
per‑day limits. The numeric limits are passed in by the caller.
"""

from __future__ import annotations

import ipaddress
import threading
import time
from typing import Dict, List

from fastapi import HTTPException

# In‑memory store of request timestamps per key. It is per process: it resets
# on restart and is not shared between workers, so run a single worker.
request_timestamps: Dict[str, List[float]] = {}

# Guards read-modify-write of ``request_timestamps`` across handler threads.
_LOCK = threading.Lock()

# Buckets keyed by client address come from unauthenticated traffic, so the
# ones with no request in the last day are dropped every this many calls.
SWEEP_EVERY = 1000
_calls_since_sweep = 0


def client_bucket(host: str | None) -> str:
    """Name the rate-limit bucket for a client address.

    IPv6 clients are grouped by /64, the block a single host usually holds,
    so rotating addresses within it does not reset the limit.
    """
    if not host:
        return "unknown"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host
    if ip.version == 6:
        if ip.ipv4_mapped:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def _sweep_stale_buckets(now: float) -> None:
    stale = [key for key, stamps in request_timestamps.items() if not stamps or now - stamps[-1] >= 86400]
    for key in stale:
        del request_timestamps[key]


def allow_request(
    key_hash: str,
    per_minute: int,
    per_day: int,
) -> None:
    """Enforce rate limits for a given key (thread-safe wrapper)."""
    global _calls_since_sweep
    with _LOCK:
        _calls_since_sweep += 1
        if _calls_since_sweep >= SWEEP_EVERY:
            _calls_since_sweep = 0
            _sweep_stale_buckets(time.time())
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