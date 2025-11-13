"""
Authentication and API key management for the currency conversion service.

Keys are stored in `keys.json` with the following fields:

- key_hash: SHA-256 hash of the raw API key
- owner: who owns / uses the key
- tier: "free" or "paid"
- active: whether the key is usable
- created_at: timestamp of creation
- plan_code: code of the paid plan (for paid keys)
- credits: remaining credits (decremented per API call)
- upgraded_at: when the key was last upgraded (optional)
"""

import json
import os
import time
import uuid
import hashlib
from typing import Dict, Any, List, Optional

from fastapi import HTTPException

KEY_FILE = "keys.json"

# Ensure keys file exists
if not os.path.exists(KEY_FILE):
    with open(KEY_FILE, "w", encoding="utf-8") as f:
        json.dump({"keys": []}, f, indent=2)


# ------------------------------------------
# Low-level helpers
# ------------------------------------------

def load_keys() -> Dict[str, Any]:
    with open(KEY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_keys(data: Dict[str, Any]) -> None:
    with open(KEY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


# ------------------------------------------
# Key generation
# ------------------------------------------

def generate_api_key(owner: str) -> str:
    """
    Generate a PAID key (usually via admin endpoint).
    Paid keys default to tier='paid' and unlimited credits (credits=None)
    until they are bound to a plan via Paystack upgrade.
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
            "created_at": time.time(),
            "plan_code": None,
            "credits": None,
        }
    )
    save_keys(data)

    return raw


def generate_free_key() -> str:
    """
    Generate a FREE key, with an initial 'free' tier and no plan_code.
    Credits will be initialised by the charging logic based on config.
    """
    raw = "free_" + uuid.uuid4().hex[:16]
    hashed = hash_key(raw)

    data = load_keys()
    data.setdefault("keys", [])
    data["keys"].append(
        {
            "key_hash": hashed,
            "owner": "free-tier",
            "tier": "free",
            "active": True,
            "created_at": time.time(),
            "plan_code": None,
            "credits": None,  # set later based on free-tier config
        }
    )
    save_keys(data)

    return raw


# ------------------------------------------
# Validation / Revocation
# ------------------------------------------

def validate_api_key(raw_key: Optional[str]) -> Dict[str, Any]:
    if not raw_key:
        raise HTTPException(status_code=401, detail="Missing API Key")

    hashed = hash_key(raw_key)
    data = load_keys()
    for key in data.get("keys", []):
        if key.get("key_hash") == hashed:
            if not key.get("active", True):
                raise HTTPException(status_code=403, detail="API key has been revoked")

            # Ensure defaults for backward compatibility
            key.setdefault("tier", "paid")
            key.setdefault("plan_code", None)

            # Attach key_hash so callers don't need to compute it again
            key_copy = dict(key)
            key_copy["key_hash"] = hashed
            return key_copy

    raise HTTPException(status_code=401, detail="Invalid API Key")


def revoke_api_key(raw_key: str) -> None:
    hashed = hash_key(raw_key)
    data = load_keys()

    for key in data.get("keys", []):
        if key.get("key_hash") == hashed:
            key["active"] = False
            save_keys(data)
            return

    raise HTTPException(status_code=404, detail="API key not found")


# ------------------------------------------
# Tier upgrades / downgrades (Paystack driven)
# ------------------------------------------

def upgrade_key_tier_by_hash(
    key_hash: str,
    new_tier: str,
    plan: Optional[Dict[str, Any]] = None,
    free_monthly_credits: int = 200,
) -> Dict[str, Any]:
    """
    Update a key's tier and plan_code based on Paystack events.

    - If new_tier == "paid" and a plan is provided, the key becomes paid,
      plan_code is set, and credits are reset to the plan's monthly_credits.
    - If new_tier == "free", the key is downgraded to free tier, plan_code is
      cleared, and credits are reset to the free-tier monthly credits.
    """

    data = load_keys()
    keys = data.get("keys", [])
    for key in keys:
        if key.get("key_hash") == key_hash:
            if new_tier == "paid":
                key["tier"] = "paid"
                if plan is not None:
                    key["plan_code"] = plan.get("code")
                    key["credits"] = plan.get("monthly_credits")
                key["upgraded_at"] = time.time()
            elif new_tier == "free":
                key["tier"] = "free"
                key["plan_code"] = None
                key["credits"] = free_monthly_credits
                key["upgraded_at"] = time.time()
            else:
                raise HTTPException(status_code=400, detail="Invalid tier specified")

            save_keys(data)
            return key

    raise HTTPException(status_code=404, detail="Key not found for upgrade")


# ------------------------------------------
# Credits charging per API call
# ------------------------------------------

def charge_call_for_key(
    key_record: Dict[str, Any],
    free_monthly_credits: int,
    plans: List[Dict[str, Any]],
) -> None:
    """
    Decrement the 'credits' counter for a given key.

    - FREE tier: credits are initialised to free_monthly_credits if missing.
    - PAID tier: credits are initialised from the plan's monthly_credits if
      missing and the key has a valid plan_code.
    - Keys with credits=None are treated as having unlimited credits.

    Raises HTTPException(402) when no credits are left.
    """
    key_hash = key_record.get("key_hash")
    if not key_hash:
        raise HTTPException(status_code=500, detail="Key hash missing from record")

    data = load_keys()
    updated = False

    for key in data.get("keys", []):
        if key.get("key_hash") != key_hash:
            continue

        tier = key.get("tier", "paid")
        credits = key.get("credits", None)

        # Determine applicable plan (if any)
        plan = None
        plan_code = key.get("plan_code")
        if plan_code:
            plan = next((p for p in plans if p.get("code") == plan_code), None)

        # FREE TIER
        if tier == "free":
            if credits is None:
                credits = free_monthly_credits
            if credits <= 0:
                raise HTTPException(
                    status_code=402,
                    detail="Free-tier credits exhausted. Please upgrade your plan.",
                )
            credits -= 1
            key["credits"] = credits
            updated = True

        # PAID TIER
        else:
            # No plan bound => treat as unlimited
            if plan is None:
                return

            if credits is None:
                credits = plan.get("monthly_credits")
            # monthly_credits may be None => unlimited
            if credits is None:
                return

            if credits <= 0:
                raise HTTPException(
                    status_code=402,
                    detail="Subscription credits exhausted. Please renew or upgrade.",
                )
            credits -= 1
            key["credits"] = credits
            updated = True

        break

    if updated:
        save_keys(data)
