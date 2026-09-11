"""
Authentication and API key management for the currency conversion service.

Keys are stored in ``keys.json`` with the following structure::

    {
        "keys": [
            {
                "key_hash": "...",    # SHA-256 hash of the raw key
                "owner": "...",      # Owner name (admin-generated or free)
                "email": "user@example.com",
                "first_name": "Jane",
                "last_name": "Doe",
                "tier": "free" | "paid",
                "active": true | false,
                "created_at": 1700000000.0,
                "plan_code": "..." | null,
                "pending_plan_code": "..." | null,
                "credits": int | null,
                "credits_reset_at": 1700000000.0 | null,
                "upgraded_at": 1700000000.0 | null,
                "paystack_subscription_code": "SUB_xxx" | null,
                "paystack_email_token": "email_token" | null,
                "subscription_status": "pending" | "active" | "canceled" | "free"
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

import time
import uuid
import hashlib
from typing import Dict, Any, List, Optional, Tuple

from fastapi import HTTPException

from storage_utils import atomic_write_json, ensure_json_file, load_json_file, synchronized

# All timestamps on key records are epoch seconds (``time.time()``), the same
# unit used for credit resets, revocations, sessions and transactions. They are
# formatted for humans at the API boundary with ``time_utils.to_wat_iso``,
# which also passes through the ISO strings written by earlier versions.

# Location of the key store
KEY_FILE = "keys.json"

# Approximate number of seconds in a month for credit resets (30 days).
SECONDS_PER_MONTH = 30 * 24 * 60 * 60

# Ensure the key store exists
ensure_json_file(KEY_FILE, "keys")


def _ensure_key_file() -> None:
    """Create the key store file if it has been deleted."""
    ensure_json_file(KEY_FILE, "keys")


def load_keys() -> Dict[str, Any]:
    """Load the keys JSON document from disk."""
    _ensure_key_file()
    return load_json_file(KEY_FILE, "keys")


def save_keys(data: Dict[str, Any]) -> None:
    """Persist the keys JSON document to disk."""
    atomic_write_json(KEY_FILE, data)


def hash_key(raw_key: str) -> str:
    """Compute the SHA-256 hash of a raw API key."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def normalize_email(email: str) -> str:
    """Lowercase and trim an email address, rejecting empty values."""
    email_norm = email.strip().lower()
    if not email_norm:
        raise HTTPException(status_code=400, detail="Email is required for API keys")
    return email_norm


def _validate_contact(email: str, first_name: str, last_name: str) -> Dict[str, str]:
    """Validate and normalize contact details for a key."""
    email_norm = normalize_email(email)
    first = first_name.strip()
    last = last_name.strip()
    if not first or not last:
        raise HTTPException(status_code=400, detail="First name and last name are required")
    return {"email": email_norm, "first_name": first, "last_name": last}


def assert_unique_key_email(email: str, skip_key_hash: Optional[str] = None) -> None:
    """Ensure no other *active* key already uses the given email."""
    email_norm = normalize_email(email)
    data = load_keys()
    for record in data.get("keys", []):
        if skip_key_hash and record.get("key_hash") == skip_key_hash:
            continue
        if (
            record.get("email")
            and record.get("email") == email_norm
            and record.get("active", True)
        ):
            prefix = (record.get("key_hash") or "")[:8]
            raise HTTPException(
                status_code=400,
                detail=f"Email is already associated with an API key (prefix {prefix}).",
            )


def find_key_by_email(
    email: str, *, include_inactive: bool = True, include_revoked: bool = True
) -> Optional[Dict[str, Any]]:
    """Return the first key record matching the provided email (if any)."""
    email_norm = normalize_email(email)
    data = load_keys()
    for record in data.get("keys", []):
        if record.get("email") != email_norm:
            continue
        if not include_inactive and not record.get("active", True):
            continue
        if not include_revoked and record.get("revoked_at"):
            continue
        copy = dict(record)
        copy["key_hash"] = record.get("key_hash")
        return copy
    return None


@synchronized
def update_key_contact(
    key_hash: str, email: str, first_name: str, last_name: str
) -> Dict[str, Any]:
    """Attach or refresh contact information on an existing key record."""
    contact = _validate_contact(email, first_name, last_name)
    assert_unique_key_email(contact["email"], skip_key_hash=key_hash)
    data = load_keys()
    for record in data.get("keys", []):
        if record.get("key_hash") == key_hash:
            record.update(contact)
            save_keys(data)
            return record
    raise HTTPException(status_code=404, detail="Key not found for contact update")


@synchronized
def mark_pending_plan(
    key_hash: str, plan_code: str, *, deactivate: bool = False
) -> Dict[str, Any]:
    """Mark a key as awaiting activation of a specific plan."""
    data = load_keys()
    for record in data.get("keys", []):
        if record.get("key_hash") != key_hash:
            continue
        record["pending_plan_code"] = plan_code
        record["subscription_status"] = "pending"
        if deactivate:
            record["active"] = False
        save_keys(data)
        return record
    raise HTTPException(status_code=404, detail="Key not found for pending plan update")


@synchronized
def generate_api_key(
    owner: str,
    email: str,
    first_name: str,
    last_name: str,
    *,
    plan_code: Optional[str] = None,
    pending_plan_code: Optional[str] = None,
    active: bool = True,
    subscription_status: str = "active",
) -> str:
    """Create a new paid API key with contact metadata.

    The returned value is the raw key (UUID). Only its hash is stored
    on disk. Paid keys default to unlimited credits until a plan is
    applied via subscription upgrade.
    """
    contact = _validate_contact(email, first_name, last_name)
    assert_unique_key_email(contact["email"])
    raw = str(uuid.uuid4())
    hashed = hash_key(raw)
    data = load_keys()
    data.setdefault("keys", [])
    record = {
        "key_hash": hashed,
        "owner": owner,
        "tier": "paid",
        "active": active,
        "created_at": time.time(),
        "plan_code": plan_code,
        "pending_plan_code": pending_plan_code,
        "credits": None,
        # No credits reset until a plan is applied
        "credits_reset_at": None,
        "paystack_subscription_code": None,
        "paystack_email_token": None,
        "subscription_status": subscription_status,
    }
    record.update(contact)
    data["keys"].append(record)
    save_keys(data)
    return raw


@synchronized
def generate_free_key(
    owner: str | None = None,
    email: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    *,
    subscription_status: str = "free",
) -> str:
    """Create a new free API key with contact metadata.

    Free keys have a distinctive prefix and start with no plan or
    credits. Credits will be initialised on first use when charging
    occurs.

    Args:
        owner: Optional identifier for the owner of the key. If
            provided, the ``owner`` field of the key record will be
            set to this value instead of the default ``"free-tier"``.
        email: Contact email uniquely tied to this key.
        first_name: Contact first name.
        last_name: Contact last name.

    Returns:
        The newly generated raw API key string. Only the key's hash
        and metadata are persisted on disk.
    """
    contact = _validate_contact(email or "", first_name or "", last_name or "")
    assert_unique_key_email(contact["email"])
    # Generate a distinctive free-key prefix so that these keys can be
    # easily identified. We use a 32-character hex string to minimise
    # collision risk while keeping the overall key length manageable.
    raw = "free_" + uuid.uuid4().hex[:32]
    hashed = hash_key(raw)
    data = load_keys()
    data.setdefault("keys", [])
    # Determine the owner string. If the caller provided an owner, use
    # that; otherwise fall back to the sentinel value "free-tier". This
    # allows user-specific free keys without altering existing behaviour
    # for anonymous free-tier keys.
    owner_str = owner if owner is not None else "free-tier"
    # Free keys start with no plan and no credits. A credits reset
    # timestamp is set to one month from now so that monthly quotas
    # reset automatically.
    record = {
        "key_hash": hashed,
        "owner": owner_str,
        "tier": "free",
        "active": True,
        "created_at": time.time(),
        "plan_code": None,
        "pending_plan_code": None,
        # Credits are initialised on first use; reset timestamp is one month from now
        "credits": None,
        "credits_reset_at": time.time() + SECONDS_PER_MONTH,
        "paystack_subscription_code": None,
        "paystack_email_token": None,
        "subscription_status": subscription_status,
    }
    record.update(contact)
    data["keys"].append(record)
    save_keys(data)
    return raw


def validate_api_key(
    raw_key: Optional[str],
    *,
    allow_inactive: bool = False,
    allow_unpaid: bool = False,
) -> Dict[str, Any]:
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
                status = record.get("subscription_status")
                if allow_inactive:
                    # For billing flows or admin actions we permit inactive keys to pass through.
                    pass
                elif status == "pending":
                    raise HTTPException(
                        status_code=402,
                        detail="Subscription is pending activation for this key",
                    )
                else:
                    raise HTTPException(status_code=403, detail="API key has been revoked")
            # Default missing tier to paid for legacy keys
            record.setdefault("tier", "paid")
            record.setdefault("subscription_status", "active" if record.get("tier") == "paid" else "free")
            record.setdefault("pending_plan_code", None)
            # Ensure contact fields exist even for legacy keys
            if "email" not in record:
                record["email"] = None
            if "first_name" not in record:
                record["first_name"] = None
            if "last_name" not in record:
                record["last_name"] = None
            # Enforce paid subscription state unless explicitly permitted. A paid
            # key whose subscription is active may legitimately have no plan:
            # admin-issued keys run on the default paid limits with unlimited
            # credits (see config.DEFAULT_PAID_* and charge_call_for_key).
            if record["tier"] == "paid":
                subscription_status = record.get("subscription_status")
                if subscription_status != "active" and not allow_unpaid:
                    raise HTTPException(
                        status_code=402,
                        detail="Paid subscription required to use this key. Complete billing to activate.",
                    )
            record_copy = dict(record)
            record_copy["key_hash"] = hashed
            return record_copy
    raise HTTPException(status_code=401, detail="Invalid API Key")


@synchronized
def revoke_api_key(raw_key: str) -> None:
    """Revoke a key so it can no longer be used."""
    hashed = hash_key(raw_key)
    data = load_keys()
    for record in data.get("keys", []):
        if record.get("key_hash") == hashed:
            record["active"] = False
            record["subscription_status"] = "revoked"
            record["pending_plan_code"] = None
            record["revoked_at"] = time.time()
            save_keys(data)
            return
    raise HTTPException(status_code=404, detail="API key not found")


@synchronized
def delete_key_by_hash(key_hash: str) -> bool:
    """Remove a key record outright.

    Only for keys that were never revealed to a caller, e.g. rolling back a
    /billing/subscribe whose Paystack checkout failed. Revocation, not
    deletion, is the right tool for keys that have been issued.
    """
    data = load_keys()
    keys = data.get("keys", [])
    remaining = [r for r in keys if r.get("key_hash") != key_hash]
    if len(remaining) == len(keys):
        return False
    data["keys"] = remaining
    save_keys(data)
    return True


@synchronized
def rotate_api_key(raw_key: str) -> Tuple[str, Dict[str, Any]]:
    """Generate a new raw key and swap the hash on an existing record.

    Returns:
        A tuple of (new_raw_key, updated_record_copy)
    """
    hashed = hash_key(raw_key)
    data = load_keys()
    for record in data.get("keys", []):
        if record.get("key_hash") != hashed:
            continue
        if record.get("tier") == "free":
            new_raw = "free_" + uuid.uuid4().hex[:32]
        else:
            new_raw = str(uuid.uuid4())
        record["key_hash"] = hash_key(new_raw)
        save_keys(data)
        updated = dict(record)
        updated["key_hash"] = record["key_hash"]
        return new_raw, updated
    raise HTTPException(status_code=404, detail="API key not found for rotation")


@synchronized
def restore_api_key(raw_key: str, subscription_status: Optional[str] = None) -> Dict[str, Any]:
    """Reactivate a previously revoked or inactive key.

    Revocation overwrites the subscription status with ``"revoked"``. When the
    caller does not say which status to restore, infer a usable one from the
    tier so the key actually works again: ``free`` for free keys and
    ``active`` for paid keys. An explicit ``subscription_status`` always wins.
    """
    hashed = hash_key(raw_key)
    data = load_keys()
    for record in data.get("keys", []):
        if record.get("key_hash") == hashed:
            record["active"] = True
            if subscription_status is not None:
                record["subscription_status"] = subscription_status
            elif record.get("subscription_status") == "revoked":
                record["subscription_status"] = "free" if record.get("tier") == "free" else "active"
            record.pop("revoked_at", None)
            save_keys(data)
            record_copy = dict(record)
            record_copy["key_hash"] = hashed
            return record_copy
    raise HTTPException(status_code=404, detail="API key not found")


@synchronized
def claim_api_key(raw_key: str, user_id: str) -> Dict[str, Any]:
    """Attach an existing key to a user account.

    Possession of the raw key is the proof of ownership, so this never
    matches on email. Idempotent when the key already belongs to the user.
    """
    hashed = hash_key(raw_key)
    data = load_keys()
    for record in data.get("keys", []):
        if record.get("key_hash") != hashed:
            continue
        if record.get("owner") != user_id:
            record["owner"] = user_id
            save_keys(data)
        record_copy = dict(record)
        record_copy["key_hash"] = hashed
        return record_copy
    raise HTTPException(status_code=404, detail="API key not found")


@synchronized
def upgrade_key_tier_by_hash(
    key_hash: str,
    new_tier: str,
    plan: dict | None = None,
    free_monthly_credits: int = 200,
    paystack_subscription_code: str | None = None,
    paystack_email_token: str | None = None,
    subscription_status: str | None = None,
    active: bool | None = None,
    allow_revoked: bool = False,
) -> dict:
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
        paystack_subscription_code: Subscription code returned by Paystack.
        paystack_email_token: Email token returned by Paystack.
        subscription_status: Optional status to store on the key.
        active: Explicit active flag to set on the key.

    Returns:
        The updated key record.
    """
    data = load_keys()
    for record in data.get('keys', []):
        if record.get('key_hash') != key_hash:
            continue
        if record.get("revoked_at") and not allow_revoked:
            raise HTTPException(status_code=403, detail="Key is revoked and cannot be upgraded")
        if new_tier == 'paid':
            record['tier'] = 'paid'
            if plan is not None:
                record['plan_code'] = plan.get('code')
                record['credits'] = plan.get('monthly_credits')
                # Set the next reset time if the plan has finite credits
                if plan.get('monthly_credits') is not None:
                    record['credits_reset_at'] = time.time() + SECONDS_PER_MONTH
                else:
                    # Unlimited credits -> no reset schedule
                    record['credits_reset_at'] = None
            # Clear pending plan once activated
            record['pending_plan_code'] = None
            # Attach Paystack subscription identifiers
            if paystack_subscription_code is not None:
                record['paystack_subscription_code'] = paystack_subscription_code
            if paystack_email_token is not None:
                record['paystack_email_token'] = paystack_email_token
            record['subscription_status'] = subscription_status or 'active'
            record['active'] = True if active is None else active
            record['upgraded_at'] = time.time()
        elif new_tier == 'free':
            record['tier'] = 'free'
            record['plan_code'] = None
            record['pending_plan_code'] = None
            record['paystack_subscription_code'] = None
            record['paystack_email_token'] = None
            record['subscription_status'] = subscription_status or 'free'
            if active is not None:
                record['active'] = active
            # Reset credits and set next reset timestamp for free tier
            record['credits'] = free_monthly_credits
            record['credits_reset_at'] = time.time() + SECONDS_PER_MONTH
            record['upgraded_at'] = time.time()
        else:
            raise HTTPException(status_code=400, detail='Invalid tier specified')
        save_keys(data)
        return record
    raise HTTPException(status_code=404, detail='Key not found for upgrade')


@synchronized
def update_subscription_identifiers(
    key_hash: str,
    *,
    paystack_subscription_code: str | None = None,
    paystack_email_token: str | None = None,
    subscription_status: str | None = None,
) -> dict:
    """Update Paystack subscription identifiers without touching credits."""
    data = load_keys()
    for record in data.get("keys", []):
        if record.get("key_hash") != key_hash:
            continue
        if paystack_subscription_code is not None:
            record["paystack_subscription_code"] = paystack_subscription_code
        if paystack_email_token is not None:
            record["paystack_email_token"] = paystack_email_token
        if subscription_status is not None:
            record["subscription_status"] = subscription_status
        save_keys(data)
        return record
    raise HTTPException(status_code=404, detail="Key not found for subscription update")

@synchronized
def charge_call_for_key(
    key_record: Dict[str, Any],
    free_monthly_credits: int,
    plans: List[Dict[str, Any]],
) -> tuple[Dict[str, Any], bool]:
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
    billed = False
    updated_record: Dict[str, Any] | None = None
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
                    # No plan -> unlimited credits
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
            billed = True
        else:
            # Paid tier logic
            # No plan: unlimited credits
            if plan is None:
                updated_record = dict(record)
                break
            # Initialise credits from plan if missing
            if credits is None:
                credits = plan.get("monthly_credits")
                record["credits"] = credits
            # If credits is None after plan, treat as unlimited
            if credits is None:
                updated_record = dict(record)
                break
            if credits <= 0:
                raise HTTPException(
                    status_code=402,
                    detail="Subscription credits exhausted. Please renew or upgrade.",
                )
            credits -= 1
            record["credits"] = credits
            updated = True
            billed = True
        updated_record = dict(record)
        break
    if updated:
        save_keys(data)
    if updated_record is None:
        raise HTTPException(status_code=404, detail="API key record missing for billing")
    # Ensure the key hash is present on the returned record
    updated_record["key_hash"] = key_hash
    return updated_record, billed
