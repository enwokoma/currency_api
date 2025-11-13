"""
Authentication and API key management for the currency conversion service.

Keys are stored in ``keys.json`` with the following structure::

    {
        "keys": [
            {
                "key_hash": "...",    # SHA‑256 hash of the raw key
                "owner": "...",      # Owner name (admin‑generated or free)
                "tier": "free" | "paid",
                "active": true | false,
                "created_at": 1700000000.0,
                "plan_code": "..." | null,
                "credits": int | null,
                "credits_reset_at": 1700000000.0 | null,
                "upgraded_at": 1700000000.0 | null
            },
            ...
        ]
    }

Free keys and paid keys behave differently:

* **Free keys**: tier="free", plan_code=None. Credits are initialised
  to ``free_monthly_credits`` on first use. Each API call decrements
  credits. When credits are exhausted, the key cannot be used until
  upgraded to a paid plan.
* **Paid keys**: tier="paid". If ``plan_code`` and ``monthly_credits``
  are defined for the plan, credits are initialised from the plan's
  monthly credits. Each API call decrements credits. If credits are
  exhausted, the user must renew or upgrade their plan. If
  ``credits`` is None, the key has unlimited billable calls.

Functions provided here handle generation of keys, validation,
revocation, tier upgrades/downgrades, and credit consumption.
"""

from __future__ import annotations

import json
import os
import time
import uuid
import hashlib
from typing import Dict, Any, List, Optional

from fastapi import HTTPException

from datetime import datetime, timezone, timedelta

# West Africa Time (UTC+1) timezone definition. Lagos does not observe DST.
WAT = timezone(timedelta(hours=1))

def now_wat_iso() -> str:
    """Return current time in West Africa Time (Lagos) as ISO without timezone.

    This returns a string formatted like ``YYYY-MM-DDTHH:MM:SS.mmm`` without
    including the ``+01:00`` offset. It still represents local time in
    Africa/Lagos, but omits the timezone suffix for readability.
    """
    # Get current time in WAT
    dt = datetime.now(WAT)
    # Remove timezone info for a cleaner representation
    naive = dt.replace(tzinfo=None)
    return naive.isoformat(timespec="milliseconds")

# Location of the key store
KEY_FILE = "keys.json"

# Approximate number of seconds in a month for credit resets (30 days).
SECONDS_PER_MONTH = 30 * 24 * 60 * 60

# Ensure the key store exists
if not os.path.exists(KEY_FILE):
    with open(KEY_FILE, "w", encoding="utf-8") as fh:
        json.dump({"keys": []}, fh, indent=2)


def load_keys() -> Dict[str, Any]:
    """Load the keys JSON document from disk."""
    with open(KEY_FILE, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_keys(data: Dict[str, Any]) -> None:
    """Persist the keys JSON document to disk."""
    with open(KEY_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def hash_key(raw_key: str) -> str:
    """Compute the SHA‑256 hash of a raw API key."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_api_key(owner: str) -> str:
    """Create a new paid API key.

    The returned value is the raw key (UUID). Only its hash is stored
    on disk. Paid keys default to unlimited credits until a plan is
    applied via subscription upgrade.
    """
    raw = str(uuid.uuid4())
    hashed = hash_key(raw)
    data = load_keys()
    data.setdefault("keys", [])
    data["keys"].append(
        {
            "key_hash": hashed,
            "owner": owner,
            "tier": "paid",
            "active": True,
            # Human‑readable creation time in WAT (ISO 8601 with ms)
            "created_at": now_wat_iso(),
            "plan_code": None,
            "credits": None,
            # No credits reset until a plan is applied
            "credits_reset_at": None,
        }
    )
    save_keys(data)
    return raw


def generate_free_key() -> str:
    """Create a new free API key.

    Free keys have a distinctive prefix and start with no plan or
    credits. Credits will be initialised on first use when charging
    occurs.
    """
    raw = "free_" + uuid.uuid4().hex[:16]
    hashed = hash_key(raw)
    data = load_keys()
    data.setdefault("keys", [])
    # Free keys start with no plan and no credits. A credits reset timestamp
    # is set to one month from now so that monthly quotas reset automatically.
    data["keys"].append(
        {
            "key_hash": hashed,
            "owner": "free-tier",
            "tier": "free",
            "active": True,
            # Human‑readable creation time in WAT
            "created_at": now_wat_iso(),
            "plan_code": None,
            # Credits are initialised on first use; reset timestamp is one month from now
            "credits": None,
            "credits_reset_at": time.time() + SECONDS_PER_MONTH,
        }
    )
    save_keys(data)
    return raw


def validate_api_key(raw_key: Optional[str]) -> Dict[str, Any]:
    """Validate a raw API key and return its record.

    Raises ``HTTPException`` if the key is missing, invalid or
    revoked. Always returns a copy of the record with ``key_hash``
    attached.
    """
    if not raw_key:
        raise HTTPException(status_code=401, detail="Missing API Key")
    hashed = hash_key(raw_key)
    data = load_keys()
    for record in data.get("keys", []):
        if record.get("key_hash") == hashed:
            if not record.get("active", True):
                raise HTTPException(status_code=403, detail="API key has been revoked")
            # Default missing tier to paid for legacy keys
            record.setdefault("tier", "paid")
            record_copy = dict(record)
            record_copy["key_hash"] = hashed
            return record_copy
    raise HTTPException(status_code=401, detail="Invalid API Key")


def revoke_api_key(raw_key: str) -> None:
    """Revoke a key so it can no longer be used."""
    hashed = hash_key(raw_key)
    data = load_keys()
    for record in data.get("keys", []):
        if record.get("key_hash") == hashed:
            record["active"] = False
            save_keys(data)
            return
    raise HTTPException(status_code=404, detail="API key not found")


def upgrade_key_tier_by_hash(
    key_hash: str,
    new_tier: str,
    plan: Optional[Dict[str, Any]] = None,
    free_monthly_credits: int = 200,
) -> Dict[str, Any]:
    """Upgrade or downgrade a key based on subscription events.

    * When ``new_tier`` is ``"paid"`` and a plan dict is provided,
      ``tier`` is set to ``"paid"``, ``plan_code`` is set from the plan,
      ``credits`` is reset to ``plan["monthly_credits"]`` (or left as
      unlimited if not provided).
    * When ``new_tier`` is ``"free"``, ``tier`` becomes ``"free"``,
      ``plan_code`` is cleared, and ``credits`` is reset to
      ``free_monthly_credits``.

    Args:
        key_hash: The hash of the key to modify.
        new_tier: "free" or "paid".
        plan: Plan definition dict used when upgrading to paid.
        free_monthly_credits: Credits assigned to free tier on reset.

    Returns:
        The updated key record.
    """
    data = load_keys()
    for record in data.get("keys", []):
        if record.get("key_hash") == key_hash:
            if new_tier == "paid":
                record["tier"] = "paid"
                if plan is not None:
                    record["plan_code"] = plan.get("code")
                    record["credits"] = plan.get("monthly_credits")
                    # Set the next reset time if the plan has finite credits
                    if plan.get("monthly_credits") is not None:
                        record["credits_reset_at"] = time.time() + SECONDS_PER_MONTH
                    else:
                        # Unlimited credits ⇒ no reset schedule
                        record["credits_reset_at"] = None
                # Record the upgrade timestamp in WAT for human readability
                record["upgraded_at"] = now_wat_iso()
            elif new_tier == "free":
                record["tier"] = "free"
                record["plan_code"] = None
                # Reset credits and set next reset timestamp for free tier
                record["credits"] = free_monthly_credits
                record["credits_reset_at"] = time.time() + SECONDS_PER_MONTH
                # Record downgrade timestamp in WAT
                record["upgraded_at"] = now_wat_iso()
            else:
                raise HTTPException(status_code=400, detail="Invalid tier specified")
            save_keys(data)
            return record
    raise HTTPException(status_code=404, detail="Key not found for upgrade")


def charge_call_for_key(
    key_record: Dict[str, Any],
    free_monthly_credits: int,
    plans: List[Dict[str, Any]],
) -> None:
    """Decrement the credits counter for a given key.

    This method ensures that free keys and paid keys consume
    'billable' credits appropriately. If credits go to zero, an
    ``HTTPException`` with status 402 is raised.

    Args:
        key_record: The validated key record from ``validate_api_key``.
        free_monthly_credits: Number of calls allowed per month for free keys.
        plans: The list of plan definitions used to find plan info.
    """
    key_hash = key_record.get("key_hash")
    if not key_hash:
        raise HTTPException(status_code=500, detail="Key hash missing from record")
    data = load_keys()
    updated = False
    for record in data.get("keys", []):
        if record.get("key_hash") != key_hash:
            continue
        tier = record.get("tier", "paid")
        credits = record.get("credits")
        plan_code = record.get("plan_code")

        # Identify plan if present
        plan = None
        if plan_code:
            plan = next((p for p in plans if p.get("code") == plan_code), None)

        # Reset credits if the current period has expired
        now = time.time()
        reset_at = record.get("credits_reset_at")
        if reset_at is not None and now >= reset_at:
            if tier == "free":
                # Reset free credits
                record["credits"] = free_monthly_credits
                record["credits_reset_at"] = now + SECONDS_PER_MONTH
            else:
                # Paid tier reset
                if plan is not None:
                    mc = plan.get("monthly_credits")
                    record["credits"] = mc
                    # Unlimited monthly credits => no reset schedule
                    if mc is None:
                        record["credits_reset_at"] = None
                    else:
                        record["credits_reset_at"] = now + SECONDS_PER_MONTH
                else:
                    # No plan → unlimited credits
                    record["credits"] = None
                    record["credits_reset_at"] = None
            # Refresh credits variable after reset
            credits = record.get("credits")

        # Free tier logic
        if tier == "free":
            # Initialise credits if none
            if credits is None:
                credits = free_monthly_credits
                record["credits"] = credits
            if credits <= 0:
                raise HTTPException(
                    status_code=402,
                    detail="Free-tier credits exhausted. Please upgrade your plan.",
                )
            credits -= 1
            record["credits"] = credits
            updated = True
        else:
            # Paid tier logic
            # No plan: unlimited credits
            if plan is None:
                break
            # Initialise credits from plan if missing
            if credits is None:
                credits = plan.get("monthly_credits")
                record["credits"] = credits
            # If credits is None after plan, treat as unlimited
            if credits is None:
                break
            if credits <= 0:
                raise HTTPException(
                    status_code=402,
                    detail="Subscription credits exhausted. Please renew or upgrade.",
                )
            credits -= 1
            record["credits"] = credits
            updated = True
        break
    if updated:
        save_keys(data)