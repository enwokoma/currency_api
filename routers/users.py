"""User-facing FastAPI routes (profile, key management, usage, transactions)."""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from services.auth_service import (
    claim_api_key,
    generate_api_key,
    generate_free_key,
    hash_key,
    load_keys,
    rotate_api_key,
    revoke_api_key,
    save_keys,
)
from billing_helpers import _normalize_contact_fields, _rate_limits_for_record, billing_key_required
from dependencies import api_key_required, session_required
from schemas import (
    ClaimKeyPayload,
    CreateKeyPayload,
    RegenerateByPrefixPayload,
    RegenerateKeyPayload,
    RevokeByPrefixPayload,
    RevokeSelfPayload,
)
from storage_utils import synchronized
from time_utils import to_wat_iso
from services.transaction_service import get_transactions_for_key
from usage_stats import get_usage_map
from services.user_service import authenticate_user, load_users, sanitize_user

user_router = APIRouter()


def _compose_usage_entry(
    record: Dict[str, Any],
    tracked: Dict[str, Any],
    per_minute: int | None,
    per_day: int | None,
) -> Dict[str, Any]:
    """Combine key metadata with tracked usage counters."""
    return {
        "owner": record.get("owner"),
        "email": record.get("email"),
        "first_name": record.get("first_name"),
        "last_name": record.get("last_name"),
        "active": record.get("active", True),
        "revoked": bool(record.get("revoked_at")),
        "tier": record.get("tier", "paid"),
        "plan_code": record.get("plan_code"),
        "pending_plan_code": record.get("pending_plan_code"),
        "subscription_status": record.get("subscription_status"),
        "remaining_credits": record.get("credits"),
        "credits_reset_at": record.get("credits_reset_at"),
        "credits_reset_at_wat": to_wat_iso(record.get("credits_reset_at")),
        "rate_limits": {
            "per_minute": per_minute,
            "per_day": per_day,
        },
        "key_prefix": (record.get("key_hash") or "")[:8] + ".",
        "created_at": to_wat_iso(record.get("created_at")),
        "upgraded_at": to_wat_iso(record.get("upgraded_at")),
        "revoked_at": to_wat_iso(record.get("revoked_at")),
        "total_requests": tracked.get("total_requests", 0),
        "billable_requests": tracked.get("billable_requests", 0),
        "credits_used": tracked.get("credits_used", 0),
        "first_request_at": to_wat_iso(tracked.get("first_request_at")),
        "last_request_at": to_wat_iso(tracked.get("last_request_at")),
    }


def _get_user_key_record_or_error(raw_key: str, user_id: str) -> Dict[str, Any]:
    """Fetch a key by raw value and ensure it belongs to the user."""
    hashed = hash_key(raw_key)
    data = load_keys()
    for record in data.get("keys", []):
        if record.get("key_hash") == hashed and record.get("owner") == user_id:
            return record
    raise HTTPException(status_code=404, detail="API key not found for this user")


def _get_user_key_by_prefix(user_id: str, key_prefix: str) -> Dict[str, Any]:
    """Find a key owned by user via key hash prefix."""
    data = load_keys()
    for record in data.get("keys", []):
        key_hash = record.get("key_hash", "")
        if record.get("owner") == user_id and key_hash.startswith(key_prefix):
            return record
    raise HTTPException(status_code=404, detail="API key not found for this user")


@synchronized
def _revoke_key_record(record: Dict[str, Any]) -> None:
    data = load_keys()
    for rec in data.get("keys", []):
        if rec.get("key_hash") == record.get("key_hash"):
            rec["active"] = False
            rec["subscription_status"] = "revoked"
            rec["pending_plan_code"] = None
            rec["revoked_at"] = time.time()
            save_keys(data)
            return
    raise HTTPException(status_code=404, detail="API key not found")


@synchronized
def _rotate_key_record(record: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    """Rotate key without knowing the raw key."""
    data = load_keys()
    for rec in data.get("keys", []):
        if rec.get("key_hash") != record.get("key_hash"):
            continue
        if rec.get("tier") == "free":
            new_raw = "free_" + uuid.uuid4().hex[:32]
        else:
            new_raw = str(uuid.uuid4())
        rec["key_hash"] = hash_key(new_raw)
        save_keys(data)
        updated = dict(rec)
        updated["key_hash"] = rec["key_hash"]
        return new_raw, updated
    raise HTTPException(status_code=404, detail="API key not found")


def _is_registered_user_id(owner: str | None) -> bool:
    """True when ``owner`` is the id of a registered user account."""
    if not owner:
        return False
    return any(u.get("id") == owner for u in load_users().get("users", []))


@user_router.get("/user", tags=["User"])
def get_current_user(user: Dict[str, Any] = Depends(session_required)) -> Dict[str, Any]:
    """Return the current user's profile (sanitized)."""
    return {"user": sanitize_user(user)}


@user_router.post("/auth/create-key", tags=["User"])
def create_key(
    payload: CreateKeyPayload,
    user: Dict[str, Any] = Depends(session_required),
) -> Dict[str, Any]:
    """Create a new API key for the current user."""
    tier_lower = payload.tier.strip().lower()
    email_raw = payload.email or user.get("email") or ""
    first_raw = payload.first_name or user.get("first_name") or ""
    last_raw = payload.last_name or user.get("last_name") or ""
    email, first_name, last_name = _normalize_contact_fields(email_raw, first_raw, last_raw)
    if tier_lower not in {"free", "paid"}:
        raise HTTPException(status_code=400, detail="Invalid key tier specified")
    if tier_lower == "free":
        raw_key = generate_free_key(
            owner=user["id"],
            email=email,
            first_name=first_name,
            last_name=last_name,
        )
        return {
            "tier": "free",
            "api_key": raw_key,
            "contact": {
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
            },
        }
    raw_key = generate_api_key(
        owner=user["id"],
        email=email,
        first_name=first_name,
        last_name=last_name,
        active=False,
        subscription_status="pending",
    )
    return {
        "tier": "paid",
        "api_key": raw_key,
        "contact": {
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
        },
        "active": False,
        "subscription_status": "pending",
        "note": "Attach a subscription using /billing/upgrade or /billing/subscribe to enable paid limits. The key stays inactive until billing completes.",
    }


@user_router.post("/user/keys/revoke", tags=["User"])
def revoke_my_key(
    payload: RevokeSelfPayload,
    user: Dict[str, Any] = Depends(session_required),
) -> Dict[str, Any]:
    """Revoke a key owned by the logged-in user after password confirmation."""
    authenticate_user(email=user["email"], password=payload.password)
    _get_user_key_record_or_error(payload.api_key, user["id"])
    revoke_api_key(payload.api_key)
    return {"status": "revoked", "message": "API key has been disabled."}


@user_router.post("/user/keys/regenerate", tags=["User"])
def regenerate_my_key(
    payload: RegenerateKeyPayload,
    user: Dict[str, Any] = Depends(session_required),
) -> Dict[str, Any]:
    """Rotate a user's API key (invalidates the old key) after password confirmation."""
    authenticate_user(email=user["email"], password=payload.password)
    record = _get_user_key_record_or_error(payload.api_key, user["id"])
    new_raw, updated_record = rotate_api_key(payload.api_key)
    return {
        "message": "API key rotated. Store the new key securely; the old key no longer works.",
        "api_key": new_raw,
        "tier": updated_record.get("tier"),
        "plan_code": updated_record.get("plan_code"),
        "subscription_status": updated_record.get("subscription_status"),
        "contact": {
            "email": record.get("email"),
            "first_name": record.get("first_name"),
            "last_name": record.get("last_name"),
        },
    }


@user_router.post("/user/keys/revoke-by-prefix", tags=["User"])
def revoke_my_key_by_prefix(
    payload: RevokeByPrefixPayload,
    user: Dict[str, Any] = Depends(session_required),
) -> Dict[str, Any]:
    """Revoke a key using its prefix (for users who misplaced the raw key)."""
    authenticate_user(email=user["email"], password=payload.password)
    record = _get_user_key_by_prefix(user["id"], payload.key_prefix)
    _revoke_key_record(record)
    return {"status": "revoked", "key_prefix": payload.key_prefix + "..."}


@user_router.post("/user/keys/regenerate-by-prefix", tags=["User"])
def regenerate_my_key_by_prefix(
    payload: RegenerateByPrefixPayload,
    user: Dict[str, Any] = Depends(session_required),
) -> Dict[str, Any]:
    """Rotate a key using its prefix (no need to provide the raw key)."""
    authenticate_user(email=user["email"], password=payload.password)
    record = _get_user_key_by_prefix(user["id"], payload.key_prefix)
    new_raw, updated_record = _rotate_key_record(record)
    return {
        "message": "API key rotated. Store the new key securely; the old key no longer works.",
        "api_key": new_raw,
        "tier": updated_record.get("tier"),
        "plan_code": updated_record.get("plan_code"),
        "subscription_status": updated_record.get("subscription_status"),
        "key_prefix": (updated_record.get("key_hash") or "")[:8] + ".",
        "contact": {
            "email": updated_record.get("email"),
            "first_name": updated_record.get("first_name"),
            "last_name": updated_record.get("last_name"),
        },
    }


@user_router.post("/user/keys/claim", tags=["User"])
def claim_key(
    payload: ClaimKeyPayload,
    user: Dict[str, Any] = Depends(session_required),
) -> Dict[str, Any]:
    """Attach a key created outside this account (public free key or subscribe).

    Possession of the raw key is the proof of ownership. Keys are never
    reassigned by email match: email verification currently returns the
    code in the API response, so an email match proves nothing.
    """
    hashed = hash_key(payload.api_key)
    current = next(
        (r for r in load_keys().get("keys", []) if r.get("key_hash") == hashed),
        None,
    )
    if not current:
        raise HTTPException(status_code=404, detail="API key not found")
    owner = current.get("owner")
    if owner != user["id"] and _is_registered_user_id(owner):
        raise HTTPException(
            status_code=409,
            detail="This key is already attached to another account.",
        )
    record = claim_api_key(payload.api_key, user["id"])
    return {
        "status": "claimed",
        "key_prefix": (record.get("key_hash") or "")[:8] + ".",
        "tier": record.get("tier"),
        "plan_code": record.get("plan_code"),
        "subscription_status": record.get("subscription_status"),
        "contact": {
            "email": record.get("email"),
            "first_name": record.get("first_name"),
            "last_name": record.get("last_name"),
        },
    }


@user_router.get("/user/keys", tags=["User"])
def list_user_keys(user: Dict[str, Any] = Depends(session_required)) -> Dict[str, Any]:
    """List all API keys owned by the current user."""
    data = load_keys()
    owned = []
    for record in data.get("keys", []):
        if record.get("owner") == user["id"]:
            owned.append(
                {
                    "tier": record.get("tier"),
                    "active": record.get("active", True),
                    "revoked": bool(record.get("revoked_at")),
                    "plan_code": record.get("plan_code"),
                    "pending_plan_code": record.get("pending_plan_code"),
                    "subscription_status": record.get("subscription_status"),
                    "email": record.get("email"),
                    "first_name": record.get("first_name"),
                    "last_name": record.get("last_name"),
                    "created_at": to_wat_iso(record.get("created_at")),
                    "revoked_at": to_wat_iso(record.get("revoked_at")),
                    "upgraded_at": to_wat_iso(record.get("upgraded_at")),
                    "key_prefix": record.get("key_hash", "")[:8] + ".",
                }
            )
    return {"keys": owned}


@user_router.get("/usage", tags=["Utility"])
def usage_snapshot(key: Dict[str, Any] = Depends(api_key_required)) -> Dict[str, Any]:
    """Return usage details for the calling API key."""
    usage_map = get_usage_map()
    tracked = usage_map.get(key.get("key_hash"), {}) or {}
    per_minute, per_day = _rate_limits_for_record(key)
    return {"usage": _compose_usage_entry(key, tracked, per_minute, per_day)}


@user_router.get("/user/usage", tags=["User"])
def list_user_usage(user: Dict[str, Any] = Depends(session_required)) -> Dict[str, Any]:
    """Return usage details for all keys owned by the session user."""
    data = load_keys()
    usage_map = get_usage_map()
    summaries = []
    for record in data.get("keys", []):
        if record.get("owner") != user["id"]:
            continue
        per_minute, per_day = _rate_limits_for_record(record)
        tracked = usage_map.get(record.get("key_hash"), {}) or {}
        summaries.append(_compose_usage_entry(record, tracked, per_minute, per_day))
    return {"usage": summaries}


@user_router.get("/transactions", tags=["Billing"])
def list_my_transactions(key: Dict[str, Any] = Depends(billing_key_required)) -> Dict[str, Any]:
    """Return the transaction history for the calling user's API key.

    Uses the billing dependency so a key that is still pending payment can
    poll its own checkout status, and so reading history costs no credit.
    """
    txns = get_transactions_for_key(key["key_hash"])
    return {"transactions": txns}
