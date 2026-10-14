"""Shared helpers for billing and subscription flows."""

from __future__ import annotations

from typing import Dict, Any, Tuple

from fastapi import HTTPException, Request

from credential_utils import header_credential
from services.auth_service import (
    validate_api_key,
    load_keys,
    mark_pending_plan,
)
from services.billing_service import initialize_transaction
from services.user_service import get_user_by_session
from config import (
    BILLING_REQUESTS_PER_DAY,
    BILLING_REQUESTS_PER_MINUTE,
    PLANS,
    DEFAULT_PAID_REQUESTS_PER_DAY,
    DEFAULT_PAID_REQUESTS_PER_MINUTE,
    FREE_REQUESTS_PER_DAY,
    FREE_REQUESTS_PER_MINUTE,
)
from services.transaction_service import record_transaction
from rate_limit import allow_request


def _normalize_contact_fields(email: str, first_name: str, last_name: str) -> tuple[str, str, str]:
    """Normalize and validate contact details."""
    email_norm = email.strip().lower()
    first = first_name.strip()
    last = last_name.strip()
    if not email_norm:
        raise HTTPException(status_code=400, detail="Email is required")
    if not first or not last:
        raise HTTPException(status_code=400, detail="First name and last name are required")
    return email_norm, first, last


def _get_plan_or_error(plan_code: str) -> Dict[str, Any]:
    """Return a plan definition or raise HTTP 400."""
    plan = next((p for p in PLANS if p["code"] == plan_code), None)
    if not plan:
        raise HTTPException(status_code=400, detail="Unknown plan_code")
    paystack_code = plan.get("paystack_plan_code")
    if not paystack_code:
        raise HTTPException(
            status_code=400,
            detail=f"Paystack plan code is not configured for plan '{plan['code']}'",
        )
    return plan


def _get_owned_key_by_prefix(user_id: str, prefix: str) -> Dict[str, Any]:
    """Pick one of the session user's own keys by key hash prefix.

    The prefix only selects among keys the session already owns. It never
    proves ownership on its own: prefixes appear in listings and logs.
    """
    prefix = prefix.strip()
    if not prefix:
        raise HTTPException(
            status_code=400,
            detail="Send X-API-KEY-PREFIX to choose which of your keys to use.",
        )
    data = load_keys()
    matches = [
        r
        for r in data.get("keys", [])
        if r.get("owner") == user_id and (r.get("key_hash") or "").startswith(prefix)
    ]
    if not matches:
        raise HTTPException(status_code=404, detail="API key not found for this user")
    if len(matches) > 1:
        raise HTTPException(
            status_code=400,
            detail="Key prefix matches more than one of your keys; send a longer prefix.",
        )
    rec = dict(matches[0])
    rec.setdefault("tier", "paid")
    rec.setdefault("subscription_status", "active" if rec.get("tier") == "paid" else "free")
    return rec


def billing_key_required(request: Request) -> Dict[str, Any]:
    """Resolve the key a billing call acts on, allowing pending/inactive keys.

    The caller must prove control of the key: either the raw key in
    ``X-API-KEY``, or a session that owns the key (``X-SESSION-TOKEN``) with
    ``X-API-KEY-PREFIX`` naming which of the user's keys to use.
    """
    raw_key = header_credential(request, "X-API-KEY", "api_key")
    session_token = header_credential(request, "X-SESSION-TOKEN", "session_token")
    if raw_key:
        key_record = validate_api_key(raw_key, allow_inactive=True, allow_unpaid=True)
    elif session_token:
        user = get_user_by_session(session_token)
        key_record = _get_owned_key_by_prefix(user["id"], request.headers.get("X-API-KEY-PREFIX") or "")
    elif request.headers.get("X-API-KEY-PREFIX"):
        raise HTTPException(
            status_code=401,
            detail="A key prefix alone is not accepted. Send X-API-KEY, or X-SESSION-TOKEN for a key you own.",
        )
    else:
        raise HTTPException(status_code=401, detail="Missing API Key")
    # Hard-block revoked or inactive keys that are not merely pending activation
    if (
        not key_record.get("active", True)
        and key_record.get("subscription_status") not in {"pending", None}
    ) or key_record.get("revoked_at"):
        raise HTTPException(
            status_code=403,
            detail="This key is inactive. Contact support or generate a new key.",
        )
    allow_request(
        key_hash=f"billing:{key_record['key_hash']}",
        per_minute=BILLING_REQUESTS_PER_MINUTE,
        per_day=BILLING_REQUESTS_PER_DAY,
    )
    return key_record


def _start_subscription_checkout(
    *,
    key_hash: str,
    plan: Dict[str, Any],
    email: str,
    first_name: str,
    last_name: str,
    deactivate: bool = False,
) -> Tuple[str, str]:
    """Mark a key as pending a plan and start Paystack checkout."""
    paystack_code = plan.get("paystack_plan_code")
    if not paystack_code:
        raise HTTPException(
            status_code=400,
            detail=f"Paystack plan code is not configured for plan '{plan['code']}'",
        )
    # Flag the key as pending upgrade (optionally deactivate until payment completes)
    mark_pending_plan(key_hash, plan["code"], deactivate=deactivate)
    auth_url, reference = initialize_transaction(
        email=email,
        amount_naira=plan["price_naira"],
        api_key_hash=key_hash,
        paystack_plan_code=paystack_code,
        metadata_plan_code=plan["code"],
        customer={"first_name": first_name, "last_name": last_name},
        extra_metadata={"email": email},
    )
    # Record this transaction as pending in the transaction history.
    record_transaction(
        api_key_hash=key_hash,
        email=email,
        plan_code=plan["code"],
        paystack_plan_code=paystack_code,
        amount_naira=plan["price_naira"],
        reference=reference,
        status="pending",
        authorization_url=auth_url,
    )
    return auth_url, reference


def _rate_limits_for_record(record: Dict[str, Any]) -> Tuple[int | None, int | None]:
    """Determine rate limits for a key record."""
    tier = record.get("tier", "paid")
    plan_code = record.get("plan_code")
    if tier == "free":
        return FREE_REQUESTS_PER_MINUTE, FREE_REQUESTS_PER_DAY
    if plan_code:
        plan = next((p for p in PLANS if p["code"] == plan_code), None)
        if plan:
            return (
                plan.get("requests_per_minute", DEFAULT_PAID_REQUESTS_PER_MINUTE),
                plan.get("requests_per_day", DEFAULT_PAID_REQUESTS_PER_DAY),
            )
    return DEFAULT_PAID_REQUESTS_PER_MINUTE, DEFAULT_PAID_REQUESTS_PER_DAY
