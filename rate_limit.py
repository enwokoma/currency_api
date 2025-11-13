"""Rate limiting logic for the currency conversion service.

This module tracks requests per API key and enforces per‑minute and
per‑day limits. The limits differ depending on the key's subscription
tier. The free tier has a much lower quota than the paid plans. Paid
plans can have custom limits defined in the `PLAN_LIMITS` dictionary.
"""

from __future__ import annotations

import time
from typing import Dict, Tuple

from fastapi import HTTPException


# In‑memory storage of timestamps for each key hash. This is a simple
# sliding window implementation. Keys are not evicted across
# restarts; for distributed deployments, a shared store like Redis
# should be used.
request_timestamps: Dict[str, list[float]] = {}


# Default rate limits for free and paid tiers (requests per minute,
# requests per day). Paid keys without a specific plan use these
FREE_LIMITS: Tuple[int, int] = (10, 200)
PAID_LIMITS: Tuple[int, int] = (100, 10_000)


# Plan‑specific rate limits. If a key has a plan_code matching one of
# these entries, the limits override the default paid limits. The key
# "requests_per_minute" corresponds to per‑minute quota, and
# "requests_per_day" to the 24‑hour quota.
PLAN_LIMITS: Dict[str, Tuple[int, int]] = {
    "BASIC_MONTHLY": (100, 10_000),
    "PRO_MONTHLY": (300, 50_000),
}


def allow_request(key_hash: str, tier: str = "paid", plan_code: str | None = None) -> None:
    """Check whether an API key is allowed to make another request.

    Maintains a list of timestamps for each key and removes entries
    older than 24 hours. Enforces per‑minute and per‑day limits
    depending on the tier and plan.

    Args:
        key_hash: The hashed API key (used to index timestamps).
        tier: The tier of the key (``"free"`` or ``"paid"``).
        plan_code: Optional plan code for paid keys. If present and
            found in `PLAN_LIMITS`, the associated limits are used.

    Raises:
        HTTPException: If the request exceeds the allotted quota.
    """
    now = time.time()
    # Initialize storage for this key if not present
    if key_hash not in request_timestamps:
        request_timestamps[key_hash] = []
    # Purge timestamps older than 24 hours
    timestamps = [t for t in request_timestamps[key_hash] if now - t < 86400]

    # Determine limits based on tier/plan
    if tier == "free":
        per_minute, per_day = FREE_LIMITS
    else:
        # Paid tier: check plan specific overrides
        if plan_code and plan_code in PLAN_LIMITS:
            per_minute, per_day = PLAN_LIMITS[plan_code]
        else:
            per_minute, per_day = PAID_LIMITS

    # Compute counts within the last minute and day
    last_minute_count = len([t for t in timestamps if now - t < 60])
    if last_minute_count >= per_minute:
        # Too many requests in the last minute
        raise HTTPException(
            status_code=429,
            detail=(
                f"Rate limit exceeded: {per_minute} requests/min. "
                "Upgrade your plan or wait before retrying."
            ),
        )
    if len(timestamps) >= per_day:
        # Too many requests in the last day
        raise HTTPException(
            status_code=429,
            detail=(
                f"Daily rate limit exceeded: {per_day} requests/day. "
                "Upgrade your plan or wait before retrying."
            ),
        )
    # Allow request; record timestamp
    timestamps.append(now)
    request_timestamps[key_hash] = timestamps