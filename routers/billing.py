"""Billing-related FastAPI routes."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status

from services.auth_service import (
    delete_key_by_hash,
    find_key_by_email,
    generate_api_key,
    hash_key,
    load_keys,
    update_key_contact,
    update_subscription_identifiers,
    upgrade_key_tier_by_hash,
)
from services.billing_service import (
    disable_subscription,
    extract_webhook_info,
    fetch_subscription_for_customer,
    verify_paystack_signature,
    verify_paystack_transaction,
)
from billing_helpers import (
    _get_plan_or_error,
    _normalize_contact_fields,
    _start_subscription_checkout,
    billing_key_required,
)
from billing_schemas import SubscribePayload, UpgradePayload, VerifyTransactionPayload
from dependencies import public_route_limit
from mailer import valid_address
from notifications import send_key_exists, send_subscription_checkout, send_subscription_failed
from config import PLANS, FREE_TIER_MONTHLY_CREDITS
from services.transaction_service import (
    find_transaction_by_reference,
    get_latest_transaction_for_key,
    get_pending_transaction_for_key,
    has_valid_authorization_url,
    record_transaction,
    update_transaction_by_reference,
    update_transactions_for_key,
)
from webhook_logs import log_webhook_event

billing_router = APIRouter(tags=["Billing"])

SUCCESS_EVENTS = {"charge.success", "subscription.create", "subscription.enable"}
FAILURE_EVENTS = {"subscription.disable", "invoice.failed", "invoice.create_failed"}


def _find_key_record(api_key_hash: str) -> Dict[str, Any] | None:
    return next(
        (record for record in load_keys().get("keys", []) if record.get("key_hash") == api_key_hash),
        None,
    )


def _resolve_plan(plan_code: str | None) -> Dict[str, Any] | None:
    if not plan_code:
        return None
    plan = next((p for p in PLANS if p.get("code") == plan_code), None)
    if plan:
        return plan
    return next((p for p in PLANS if p.get("paystack_plan_code") == plan_code), None)


def _upsert_transaction_record(
    *,
    reference: str | None,
    api_key_hash: str,
    email: str | None,
    plan: Dict[str, Any],
    status: str,
    subscription_code: str | None,
    email_token: str | None,
    amount_kobo: int | None = None,
) -> Dict[str, Any] | None:
    if not reference:
        return None
    existing = find_transaction_by_reference(reference)
    if existing:
        return (
            update_transaction_by_reference(
                reference,
                status=status,
                subscription_code=subscription_code,
                email_token=email_token,
            )
            or existing
        )
    amount_naira = plan.get("price_naira") or 0
    if amount_kobo is not None:
        try:
            amount_naira = int(round(float(amount_kobo) / 100))
        except (TypeError, ValueError):
            pass
    return record_transaction(
        api_key_hash=api_key_hash,
        email=email or "",
        plan_code=plan["code"],
        paystack_plan_code=plan["paystack_plan_code"],
        amount_naira=amount_naira,
        reference=reference,
        status=status,
        subscription_code=subscription_code,
        email_token=email_token,
    )


def _sync_subscription_identifiers(
    *,
    api_key_hash: str,
    reference: str | None,
    subscription_code: str | None,
    email_token: str | None,
) -> None:
    if subscription_code is None and email_token is None:
        return
    update_subscription_identifiers(
        api_key_hash,
        paystack_subscription_code=subscription_code,
        paystack_email_token=email_token,
    )
    if reference:
        update_transaction_by_reference(
            reference,
            subscription_code=subscription_code,
            email_token=email_token,
        )


@billing_router.get("/plans")
def list_plans() -> Dict[str, Any]:
    """Public endpoint to list available subscription plans."""
    return {"plans": PLANS}


def _subscribe_by_email(plan: Dict[str, Any], email: str, first: str, last: str) -> None:
    """Background half of /billing/subscribe: issue the key and checkout, by email."""
    if find_key_by_email(email, include_inactive=True, include_revoked=True):
        send_key_exists(email, plan["name"])
        return
    try:
        raw_key = generate_api_key(
            owner=email,
            email=email,
            first_name=first,
            last_name=last,
            active=False,
            subscription_status="pending",
            pending_plan_code=plan["code"],
        )
    except HTTPException:
        # Lost a race with a concurrent request for the same address.
        send_key_exists(email, plan["name"])
        return
    key_hash = hash_key(raw_key)
    try:
        auth_url, reference = _start_subscription_checkout(
            key_hash=key_hash,
            plan=plan,
            email=email,
            first_name=first,
            last_name=last,
            deactivate=True,
        )
    except Exception as exc:
        # The key was never delivered, so leaving the record behind would only
        # reserve the email and block a retry.
        delete_key_by_hash(key_hash)
        print(f"[billing] Subscription checkout failed: {exc}")
        send_subscription_failed(email, plan)
        return
    send_subscription_checkout(email, raw_key, plan, auth_url, reference)


@billing_router.post(
    "/billing/subscribe",
    response_model=Dict[str, Any],
    status_code=202,
    dependencies=[Depends(public_route_limit("subscribe"))],
)
def billing_subscribe(payload: SubscribePayload, background: BackgroundTasks) -> Dict[str, Any]:
    """Email a new paid key and its Paystack checkout link (public).

    The key and link go only to the address given, and the answer is the
    same, and sent before any lookup or Paystack call, whether or not the
    address already has a key. Its owner gets a notice instead.
    """
    plan = _get_plan_or_error(payload.plan_code)
    email, first, last = _normalize_contact_fields(
        payload.email, payload.first_name, payload.last_name
    )
    if not valid_address(email):
        raise HTTPException(status_code=400, detail="Enter a valid email address")
    background.add_task(_subscribe_by_email, plan, email, first, last)
    return {
        "tier": "paid",
        "plan": plan,
        "message": "Check your inbox. The API key and checkout link have been emailed to this address.",
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


@billing_router.post("/billing/verify", response_model=Dict[str, Any])
def verify_billing_transaction(
    payload: VerifyTransactionPayload,
    key: Dict[str, Any] = Depends(billing_key_required),
) -> Dict[str, Any]:
    """Verify a Paystack transaction reference and apply the upgrade."""
    reference = payload.reference.strip()
    if not reference:
        raise HTTPException(status_code=400, detail="reference is required")
    existing_txn = find_transaction_by_reference(reference)
    if existing_txn and existing_txn.get("api_key_hash") not in (None, key["key_hash"]):
        raise HTTPException(status_code=403, detail="Reference does not belong to this API key")
    expected_plan_code = key.get("pending_plan_code")
    verified_txn = verify_paystack_transaction(
        reference,
        expected_api_key_hash=key["key_hash"],
        expected_plan_code=expected_plan_code,
    )
    metadata = verified_txn.get("metadata", {}) or {}
    plan_code = metadata.get("plan_code") or metadata.get("plan")
    if not plan_code:
        raise HTTPException(status_code=400, detail="Paystack metadata missing plan_code")
    plan = next((p for p in PLANS if p["code"] == plan_code), None)
    if not plan:
        raise HTTPException(status_code=400, detail="Plan from Paystack metadata is not recognised")
    subscription = verified_txn.get("subscription", {}) or {}
    subscription_code = (
        verified_txn.get("subscription_code")
        or subscription.get("subscription_code")
        or subscription.get("code")
    )
    email_token = verified_txn.get("email_token") or subscription.get("email_token")
    customer = verified_txn.get("customer", {}) or {}
    customer_email = customer.get("email") or metadata.get("email") or key.get("email")
    if plan and (not subscription_code or not email_token):
        customer_id = customer.get("id")
        if customer_id:
            sub_code, token = fetch_subscription_for_customer(
                customer_id,
                plan.get("paystack_plan_code"),
            )
            subscription_code = subscription_code or sub_code
            email_token = email_token or token
    amount_kobo = verified_txn.get("amount")

    already_active = (
        key.get("tier") == "paid"
        and key.get("subscription_status") == "active"
        and key.get("plan_code") == plan["code"]
    )
    if existing_txn and existing_txn.get("status") == "success" and already_active:
        _sync_subscription_identifiers(
            api_key_hash=key["key_hash"],
            reference=reference,
            subscription_code=subscription_code,
            email_token=email_token,
        )
        return {
            "status": "ok",
            "message": "Transaction already verified for this key.",
            "reference": reference,
            "plan_code": plan["code"],
        }
    if key.get("revoked_at"):
        _upsert_transaction_record(
            reference=reference,
            api_key_hash=key["key_hash"],
            email=customer_email,
            plan=plan,
            status="failed",
            subscription_code=subscription_code,
            email_token=email_token,
            amount_kobo=amount_kobo,
        )
        return {
            "status": "ignored",
            "message": "Key is revoked; transaction marked failed.",
            "reference": reference,
            "plan_code": plan["code"],
        }
    if not already_active:
        upgrade_key_tier_by_hash(
            key_hash=key["key_hash"],
            new_tier="paid",
            plan=plan,
            free_monthly_credits=FREE_TIER_MONTHLY_CREDITS,
            paystack_subscription_code=subscription_code,
            paystack_email_token=email_token,
            subscription_status="active",
            active=True,
        )
    _sync_subscription_identifiers(
        api_key_hash=key["key_hash"],
        reference=reference,
        subscription_code=subscription_code,
        email_token=email_token,
    )
    updated_txn = _upsert_transaction_record(
        reference=reference,
        api_key_hash=key["key_hash"],
        email=customer_email,
        plan=plan,
        status="success",
        subscription_code=subscription_code,
        email_token=email_token,
        amount_kobo=amount_kobo,
    )
    return {
        "status": "verified",
        "message": "Transaction verified and applied.",
        "reference": reference,
        "plan_code": plan["code"],
        "transaction": updated_txn,
    }


@billing_router.post("/paystack/webhook", status_code=status.HTTP_200_OK, tags=["Billing"])
async def paystack_webhook(request: Request) -> Dict[str, str]:
    """Handle Paystack webhook events for subscription management."""
    body = await request.body()
    log_webhook_event(body)
    signature = request.headers.get("x-paystack-signature")
    if not verify_paystack_signature(body, signature):
        raise HTTPException(status_code=400, detail="Invalid Paystack signature")
    event_type, api_key_hash, plan_code, info = extract_webhook_info(body)
    reference = info.get("reference")
    subscription_code = info.get("subscription_code")
    email_token = info.get("email_token")
    customer_email = info.get("customer_email")
    amount_kobo = None
    customer_id = None

    if event_type == "charge.success":
        if not reference:
            raise HTTPException(status_code=400, detail="Missing Paystack reference")
        verified_txn = verify_paystack_transaction(
            reference,
            expected_api_key_hash=api_key_hash,
            expected_plan_code=plan_code,
        )
        metadata = verified_txn.get("metadata", {}) or {}
        api_key_hash = metadata.get("api_key_hash") or api_key_hash
        plan_code = metadata.get("plan_code") or metadata.get("plan") or plan_code
        subscription = verified_txn.get("subscription", {}) or {}
        subscription_code = (
            subscription_code
            or verified_txn.get("subscription_code")
            or subscription.get("subscription_code")
            or subscription.get("code")
        )
        email_token = email_token or verified_txn.get("email_token") or subscription.get("email_token")
        customer = verified_txn.get("customer", {}) or {}
        customer_id = customer.get("id")
        customer_email = customer_email or customer.get("email") or metadata.get("email")
        amount_kobo = verified_txn.get("amount")
    elif event_type in {"subscription.create", "subscription.enable"} and reference:
        verified_txn = verify_paystack_transaction(
            reference,
            expected_api_key_hash=api_key_hash,
            expected_plan_code=plan_code,
        )
        metadata = verified_txn.get("metadata", {}) or {}
        api_key_hash = metadata.get("api_key_hash") or api_key_hash
        plan_code = metadata.get("plan_code") or metadata.get("plan") or plan_code
        subscription = verified_txn.get("subscription", {}) or {}
        subscription_code = (
            subscription_code
            or verified_txn.get("subscription_code")
            or subscription.get("subscription_code")
            or subscription.get("code")
        )
        email_token = email_token or verified_txn.get("email_token") or subscription.get("email_token")
        customer = verified_txn.get("customer", {}) or {}
        customer_id = customer.get("id")
        customer_email = customer_email or customer.get("email") or metadata.get("email")
        amount_kobo = verified_txn.get("amount")

    if event_type in SUCCESS_EVENTS:
        if not api_key_hash and customer_email:
            key_record = find_key_by_email(
                customer_email,
                include_inactive=True,
                include_revoked=True,
            )
            if key_record:
                api_key_hash = key_record.get("key_hash")
        if not api_key_hash:
            return {"status": "ok"}
        record = _find_key_record(api_key_hash)
        if not record:
            return {"status": "ok"}
        plan = _resolve_plan(plan_code)
        if not plan and record.get("pending_plan_code"):
            plan = _resolve_plan(record.get("pending_plan_code"))
        if not plan and record.get("plan_code"):
            plan = _resolve_plan(record.get("plan_code"))
        if not plan:
            return {"status": "ok"}
        existing_txn = find_transaction_by_reference(reference) if reference else None
        if existing_txn and existing_txn.get("api_key_hash") not in (None, api_key_hash):
            raise HTTPException(status_code=400, detail="Transaction reference does not match API key")
        already_active = (
            record.get("tier") == "paid"
            and record.get("subscription_status") == "active"
            and record.get("plan_code") == plan["code"]
        )
        if plan and (subscription_code is None or email_token is None) and customer_id:
            sub_code, token = fetch_subscription_for_customer(
                customer_id,
                plan.get("paystack_plan_code"),
            )
            subscription_code = subscription_code or sub_code
            email_token = email_token or token
        if not reference and already_active:
            _sync_subscription_identifiers(
                api_key_hash=api_key_hash,
                reference=reference,
                subscription_code=subscription_code,
                email_token=email_token,
            )
            return {"status": "ok"}
        if existing_txn and existing_txn.get("status") == "success" and already_active:
            _sync_subscription_identifiers(
                api_key_hash=api_key_hash,
                reference=reference,
                subscription_code=subscription_code,
                email_token=email_token,
            )
            return {"status": "ok"}
        if record.get("revoked_at"):
            if reference:
                _upsert_transaction_record(
                    reference=reference,
                    api_key_hash=api_key_hash,
                    email=customer_email or record.get("email"),
                    plan=plan,
                    status="failed",
                    subscription_code=subscription_code,
                    email_token=email_token,
                    amount_kobo=amount_kobo,
                )
            else:
                update_transactions_for_key(api_key_hash, new_status="failed")
            return {"status": "ok"}
        upgrade_key_tier_by_hash(
            key_hash=api_key_hash,
            new_tier="paid",
            plan=plan,
            free_monthly_credits=FREE_TIER_MONTHLY_CREDITS,
            paystack_subscription_code=subscription_code,
            paystack_email_token=email_token,
            subscription_status="active",
            active=True,
        )
        if reference:
            _upsert_transaction_record(
                reference=reference,
                api_key_hash=api_key_hash,
                email=customer_email or record.get("email"),
                plan=plan,
                status="success",
                subscription_code=subscription_code,
                email_token=email_token,
                amount_kobo=amount_kobo,
            )
        else:
            update_transactions_for_key(api_key_hash, new_status="success")
    if event_type in FAILURE_EVENTS:
        if not api_key_hash:
            return {"status": "ok"}
        record = _find_key_record(api_key_hash)
        if not record:
            return {"status": "ok"}
        status_str = "cancelled" if event_type == "subscription.disable" else "failed"
        if (
            record.get("tier") == "free"
            and record.get("subscription_status") in {"canceled", "free"}
            and not record.get("pending_plan_code")
        ):
            if reference:
                update_transaction_by_reference(
                    reference,
                    status=status_str,
                    subscription_code=subscription_code,
                    email_token=email_token,
                )
            else:
                update_transactions_for_key(api_key_hash, new_status=status_str)
            return {"status": "ok"}
        upgrade_key_tier_by_hash(
            key_hash=api_key_hash,
            new_tier="free",
            plan=None,
            free_monthly_credits=FREE_TIER_MONTHLY_CREDITS,
            subscription_status="canceled",
            active=True,
        )
        if reference:
            plan = next((p for p in PLANS if p["code"] == plan_code), None) if plan_code else None
            if plan:
                _upsert_transaction_record(
                    reference=reference,
                    api_key_hash=api_key_hash,
                    email=record.get("email"),
                    plan=plan,
                    status=status_str,
                    subscription_code=subscription_code,
                    email_token=email_token,
                )
            else:
                update_transaction_by_reference(
                    reference,
                    status=status_str,
                    subscription_code=subscription_code,
                    email_token=email_token,
                )
        else:
            update_transactions_for_key(api_key_hash, new_status=status_str)
    return {"status": "ok"}
