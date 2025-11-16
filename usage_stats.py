"""Usage tracking helpers for API keys.

This module persists per-key usage metrics, including total request
counts, billable (credit-consuming) requests, and the latest credit
balances. Metrics are stored in ``usage.json`` with the following
structure::

    {
        "usage": [
            {
                "key_hash": "...",
                "owner": "user-id",
                "email": "user@example.com",
                "first_name": "First",
                "last_name": "Last",
                "tier": "free" | "paid",
                "plan_code": "BASIC_MONTHLY",
                "subscription_status": "pending" | "active" | "canceled" | "free",
                "total_requests": 0,
                "billable_requests": 0,
                "credits_used": 0,
                "credits_remaining": 0 | null,
                "credits_reset_at": 1700000000.0,
                "first_request_at": 1700000000.0,
                "last_request_at": 1700000000.0
            }
        ]
    }

The helpers here are intentionally lightweight and avoid external
dependencies. All timestamps are stored as epoch seconds to simplify
math; callers can convert to human-readable strings as needed.
"""

from __future__ import annotations

import json
import os
import time
from typing import Dict, Any

USAGE_FILE = "usage.json"


def _ensure_file() -> None:
    """Create the usage file with an empty structure if missing."""
    if not os.path.exists(USAGE_FILE):
        with open(USAGE_FILE, "w", encoding="utf-8") as fh:
            json.dump({"usage": []}, fh, indent=2)


def load_usage() -> Dict[str, Any]:
    """Load the usage JSON document."""
    _ensure_file()
    with open(USAGE_FILE, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_usage(data: Dict[str, Any]) -> None:
    """Persist the usage JSON document."""
    with open(USAGE_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def get_usage_map() -> Dict[str, Dict[str, Any]]:
    """Return a mapping from key_hash to usage stats."""
    data = load_usage()
    return {
        entry["key_hash"]: entry
        for entry in data.get("usage", [])
        if entry.get("key_hash")
    }


def record_usage_event(
    *,
    key_hash: str,
    owner: str | None,
    email: str | None,
    first_name: str | None,
    last_name: str | None,
    tier: str,
    plan_code: str | None,
    subscription_status: str | None,
    billed: bool,
    remaining_credits: int | None,
    credits_reset_at: float | None,
) -> Dict[str, Any]:
    """Increment usage counters for a key and persist them."""
    _ensure_file()
    data = load_usage()
    usage_list = data.setdefault("usage", [])
    now = time.time()
    entry = next((u for u in usage_list if u.get("key_hash") == key_hash), None)
    if not entry:
        entry = {
            "key_hash": key_hash,
            "total_requests": 0,
            "billable_requests": 0,
            "credits_used": 0,
            "first_request_at": now,
        }
        usage_list.append(entry)
    entry["owner"] = owner
    entry["email"] = email
    entry["first_name"] = first_name
    entry["last_name"] = last_name
    entry["tier"] = tier
    entry["plan_code"] = plan_code
    entry["subscription_status"] = subscription_status

    entry["total_requests"] = entry.get("total_requests", 0) + 1
    if billed:
        entry["billable_requests"] = entry.get("billable_requests", 0) + 1
        if remaining_credits is not None:
            entry["credits_used"] = entry.get("credits_used", 0) + 1
    entry["credits_remaining"] = remaining_credits
    entry["credits_reset_at"] = credits_reset_at
    entry["last_request_at"] = now

    save_usage(data)
    return entry
