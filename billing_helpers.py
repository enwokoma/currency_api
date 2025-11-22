"""Shared helpers for billing and subscription flows."""

from __future__ import annotations

from typing import Dict, Any, Tuple

from fastapi import HTTPException, Request

from services.auth_service import (
    validate_api_key,
    load_keys,
    mark_pending_plan,
    hash_key,
)
from services.billing_service import initialize_transaction
from config import (
    PLANS,
    DEFAULT_PAID_REQUESTS_PER_DAY,
    DEFAULT_PAID_REQUESTS_PER_MINUTE,
    FREE_REQUESTS_PER_DAY,
    FREE_REQUESTS_PER_MINUTE,
)
from services.transaction_service import record_transaction


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


def _get_key_by_prefix(prefix: str) -> Dict[str, Any]:
    """Find a key globally via key hash prefix (requires uniqueness)."""
    data = load_keys()
    matches = [r for r in data.get("keys", []) if (r.get("key_hash") or "").startswith(prefix)]
    if not matches:
        raise HTTPException(status_code=401, detail="Invalid API key prefix")
    if len(matches) > 1:
        raise HTTPException(status_code=400, detail="API key prefix is not unique; provide full key")
    rec = dict(matches[0])
    rec["key_hash"] = matches[0].get("key_hash")
    return rec


def billing_key_required(request: Request) -> Dict[str, Any]:
    """Allow pending/inactive keys to initiate billing flows."""
    raw_key = request.headers.get("X-API-KEY") or request.query_params.get("api_key")
    prefix = request.headers.get("X-API-KEY-PREFIX") or request.query_params.get("api_key_prefix")
    if raw_key:
        key_record = validate_api_key(raw_key, allow_inactive=True, allow_unpaid=True)
    elif prefix:
        key_record = _get_key_by_prefix(prefix)
        # Apply basic validation similar to validate_api_key for billing flows
        if not key_record.get("active", True) and key_record.get("subscription_status") not in {"pending", None}:
            raise HTTPException(
                status_code=403,
                detail="This key is inactive. Contact support or generate a new key.",
            )
        key_record.setdefault("tier", "paid")
        key_record.setdefault("subscription_status", "active" if key_record.get("tier") == "paid" else "free")
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
