"""Authentication and API key management for the currency conversion service.

This module encapsulates all logic related to API keys. Keys are stored
in a simple JSON file on disk (`keys.json`). Each entry in the file
contains the SHA‑256 hash of the raw key, the owner, the tier (free or
paid), and timestamps. Functions are provided to create keys, validate
incoming keys, revoke keys and perform tier upgrades. Upgrades are
triggered by the billing webhook when a Paystack subscription is
successful.
"""

from __future__ import annotations

import json
import os
import time
import uuid
import hashlib
from typing import Dict, Any, Optional

from fastapi import HTTPException


# The location of the API key storage. If this file does not exist,
# it will be created automatically with an empty key list.
KEY_FILE = "keys.json"

# Ensure the key file exists on module import
if not os.path.exists(KEY_FILE):
    with open(KEY_FILE, "w", encoding="utf-8") as fh:
        json.dump({"keys": []}, fh, indent=2)


def load_keys() -> Dict[str, Any]:
    """Load the API keys JSON document from disk.

    Returns:
        A dictionary with a single key "keys" mapping to a list of key
        records.
    """
    with open(KEY_FILE, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_keys(data: Dict[str, Any]) -> None:
    """Persist the API keys JSON document to disk."""
    with open(KEY_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def hash_key(raw_key: str) -> str:
    """Compute the SHA‑256 hash of a raw API key.

    Args:
        raw_key: The unhashed API key provided to the client.

    Returns:
        A hex digest string of the SHA‑256 hash.
    """
    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_api_key(owner: str) -> str:
    """Generate a new paid API key for a given owner.

    Admin‑generated keys are considered paid by default and can enjoy
    higher rate limits. The raw key is returned to the caller but
    only its hash is stored on disk.

    Args:
        owner: A descriptive name for the key owner (e.g. a team or
            service).

    Returns:
        The raw API key (a UUID string) that should be given to the
        client. This value is only returned once and never stored in
        plaintext.
    """
    raw = str(uuid.uuid4())
    hashed = hash_key(raw)
    data = load_keys()
    data["keys"].append(
        {
            "key_hash": hashed,
            "owner": owner,
            "tier": "paid",
            "active": True,
            "created_at": time.time(),
        }
    )
    save_keys(data)
    return raw


def generate_free_key() -> str:
    """Generate a new free‑tier API key.

    Free keys have a distinctive prefix (``free_``) and are subject to
    lower rate limits. The raw key is returned to the client once.

    Returns:
        The raw free API key (with prefix ``free_``) to give to the
        caller.
    """
    raw = "free_" + uuid.uuid4().hex[:16]
    hashed = hash_key(raw)
    data = load_keys()
    data["keys"].append(
        {
            "key_hash": hashed,
            "owner": "free-tier",
            "tier": "free",
            "active": True,
            "created_at": time.time(),
        }
    )
    save_keys(data)
    return raw


def validate_api_key(raw_key: Optional[str]) -> Dict[str, Any]:
    """Validate an incoming API key.

    If the key is present in the key file and active, its record
    metadata is returned. If the key is missing or invalid, an
    HTTPException is raised.

    Args:
        raw_key: The raw API key from the client (may be None).

    Returns:
        A copy of the key record with ``key_hash`` included for
        downstream usage. Ensures the ``tier`` field exists on older
        records.
    """
    if not raw_key:
        raise HTTPException(status_code=401, detail="Missing API Key")
    hashed = hash_key(raw_key)
    data = load_keys()
    for record in data.get("keys", []):
        if record.get("key_hash") == hashed:
            if not record.get("active", True):
                raise HTTPException(status_code=403, detail="API key revoked")
            # For backwards compatibility: default to paid tier
            if "tier" not in record:
                record["tier"] = "paid"
            # Return a copy to avoid accidental mutation
            record_copy = dict(record)
            record_copy["key_hash"] = hashed
            return record_copy
    raise HTTPException(status_code=401, detail="Invalid API Key")


def revoke_api_key(raw_key: str) -> None:
    """Mark an API key as inactive/revoked.

    Args:
        raw_key: The raw API key to revoke.

    Raises:
        HTTPException: If the key does not exist.
    """
    hashed = hash_key(raw_key)
    data = load_keys()
    for record in data.get("keys", []):
        if record.get("key_hash") == hashed:
            record["active"] = False
            save_keys(data)
            return
    raise HTTPException(status_code=404, detail="API key not found")


def upgrade_key_tier_by_hash(key_hash: str, new_tier: str, plan_code: Optional[str] = None) -> Dict[str, Any]:
    """Upgrade (or downgrade) an API key by hash.

    This function updates the ``tier`` and ``plan_code`` fields of the
    matching key record. It is typically invoked by the billing
    webhook when a subscription is created or cancelled.

    Args:
        key_hash: The hash of the API key to upgrade.
        new_tier: The new tier to assign (e.g. ``"paid"`` or ``"free"``).
        plan_code: Optional Paystack plan code to record on the key. If
            None, the plan_code field will be removed.

    Returns:
        The updated key record.

    Raises:
        HTTPException: If the key is not found.
    """
    data = load_keys()
    for record in data.get("keys", []):
        if record.get("key_hash") == key_hash:
            record["tier"] = new_tier
            # Update or remove plan_code
            if plan_code:
                record["plan_code"] = plan_code
            else:
                record.pop("plan_code", None)
            record["upgraded_at"] = time.time()
            save_keys(data)
            return record
    raise HTTPException(status_code=404, detail="Key not found for upgrade")