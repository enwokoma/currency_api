"""Billing-related FastAPI routes."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from auth import (
    find_key_by_email,
    generate_api_key,
    update_key_contact,
    upgrade_key_tier_by_hash,
)
from billing import disable_subscription
from billing_helpers import (
    _get_plan_or_error,
    _normalize_contact_fields,
    _start_subscription_checkout,
    billing_key_required,
)
from billing_schemas import SubscribePayload, UpgradePayload
from config import PLANS, FREE_TIER_MONTHLY_CREDITS
from transactions import (
    get_latest_transaction_for_key,
    get_pending_transaction_for_key,
    has_valid_authorization_url,
    update_transactions_for_key,
)

billing_router = APIRouter(tags=["Billing"])


@billing_router.get("/plans")
def list_plans() -> Dict[str, Any]:
    """Public endpoint to list available subscription plans."""
    return {"plans": PLANS}


@billing_router.post("/billing/subscribe", response_model=Dict[str, Any])
def billing_subscribe(payload: SubscribePayload) -> Dict[str, Any]:
    """Create a paid key and immediately start Paystack checkout (public)."""
    plan = _get_plan_or_error(payload.plan_code)
    email, first, last = _normalize_contact_fields(
        payload.email, payload.first_name, payload.last_name
    )
    # Ensure the email is not already tied to another key
    existing = find_key_by_email(email, include_inactive=True, include_revoked=True)
    if existing:
        raise HTTPException(
            status_code=400,
            detail="Email already has an API key. Use /billing/upgrade with that key.",
        )
    owner = payload.owner or email
    raw_key = generate_api_key(
        owner=owner,
        email=email,
        first_name=first,
        last_name=last,
        active=False,
        subscription_status="pending",
        pending_plan_code=plan["code"],
    )
    record = find_key_by_email(email)
    key_hash = record.get("key_hash") if record else None
    if not key_hash:
        raise HTTPException(status_code=500, detail="Failed to persist generated key")
    auth_url, reference = _start_subscription_checkout(
        key_hash=key_hash,
        plan=plan,
        email=email,
        first_name=first,
        last_name=last,
        deactivate=True,
    )
    return {
        "tier": "paid",
        "api_key": raw_key,
        "plan": plan,
        "authorization_url": auth_url,
        "reference": reference,
        "message": "Complete payment to activate this key. It remains inactive until Paystack confirms.",
    }


@billing_router.post("/billing/upgrade", response_model=Dict[str, Any])
def billing_upgrade(
    payload: UpgradePayload,
    key: Dict[str, Any] = Depends(billing_key_required),
) -> Dict[str, Any]:
    """Start a Paystack transaction to upgrade a free key to a paid plan.

    The caller must supply a ``plan_code`` that exists in ``PLANS``.
    Contact information is taken from the payload if provided, else
    from the key record. The API key used for authentication will
    determine which key is upgraded.
    """
    plan = _get_plan_or_error(payload.plan_code)
    email = (payload.email or key.get("email") or "").strip().lower()
    first = payload.first_name or key.get("first_name")
    last = payload.last_name or key.get("last_name")
    if not email or not first or not last:
        raise HTTPException(
            status_code=400,
            detail="Email, first_name and last_name are required to upgrade.",
        )
    # Block duplicate/pending upgrades
    if key.get("pending_plan_code"):
        pending_txn = get_pending_transaction_for_key(key["key_hash"])
        if has_valid_authorization_url(pending_txn):
            return {
                "authorization_url": pending_txn.get("authorization_url"),
                "reference": pending_txn.get("reference"),
                "plan": plan,
                "message": "Continue the existing pending payment to activate your plan.",
            }
        last_txn = get_latest_transaction_for_key(key["key_hash"])
        if has_valid_authorization_url(last_txn):
            return {
                "authorization_url": last_txn.get("authorization_url"),
                "reference": last_txn.get("reference"),
                "plan": plan,
                "message": "Continue the existing pending payment to activate your plan.",
            }
        # No transaction found; re-start checkout using the pending plan
        auth_url, reference = _start_subscription_checkout(
            key_hash=key["key_hash"],
            plan=plan,
            email=email,
            first_name=first,
            last_name=last,
            deactivate=False,
        )
        return {
            "authorization_url": auth_url,
            "reference": reference,
            "plan": plan,
            "message": "Existing checkout link expired or is missing. A new link was created to continue activation.",
        }
    # Block re-purchasing the same active plan unless credits are exhausted
    if key.get("plan_code") == plan["code"] and key.get("subscription_status") == "active":
        credits = key.get("credits")
        if credits is None or credits > 0:
            raise HTTPException(
                status_code=400,
                detail="Key already has this active plan. Choose a higher plan or wait for renewal.",
            )
    # Ensure the key carries the latest contact info
    update_key_contact(key["key_hash"], email=email, first_name=first, last_name=last)
    auth_url, reference = _start_subscription_checkout(
        key_hash=key["key_hash"],
        plan=plan,
        email=email,
        first_name=first,
        last_name=last,
        deactivate=False,
    )
    return {
        "authorization_url": auth_url,
        "reference": reference,
        "plan": plan,
        "message": "Complete the payment to activate your paid plan.",
    }


@billing_router.post("/billing/cancel", response_model=Dict[str, Any])
def cancel_subscription(
    key: Dict[str, Any] = Depends(billing_key_required),
) -> Dict[str, Any]:
    """Cancel/deactivate the current subscription for the calling key."""
    sub_code = key.get("paystack_subscription_code")
    email_token = key.get("paystack_email_token")
    if not sub_code or not email_token:
        raise HTTPException(
            status_code=400,
            detail="No active Paystack subscription is associated with this key.",
        )
    disable_subscription(sub_code, email_token)
    upgrade_key_tier_by_hash(
        key_hash=key["key_hash"],
        new_tier="free",
        plan=None,
        free_monthly_credits=FREE_TIER_MONTHLY_CREDITS,
        subscription_status="canceled",
        active=True,
    )
    update_transactions_for_key(key["key_hash"], new_status="cancelled")
    return {
        "status": "ok",
        "message": "Subscription has been cancelled. The key has been downgraded to free tier.",
    }
