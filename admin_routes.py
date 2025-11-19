"""Admin FastAPI routes."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from auth import (
    generate_api_key,
    revoke_api_key,
    restore_api_key,
    load_keys,
    upgrade_key_tier_by_hash,
)
from billing import verify_transaction_reference
from config import FREE_TIER_MONTHLY_CREDITS, PLANS
from dependencies import admin_required
from schemas import (
    OwnerPayload,
    ReconcileTransactionPayload,
    RevokePayload,
    RestorePayload,
)
from time_utils import to_wat_iso
from transactions import (
    find_transaction_by_reference,
    load_transactions,
    update_transaction_by_reference,
    update_transactions_for_key,
)

admin_router = APIRouter(tags=["Admin"])


@admin_router.post("/admin/reconcile-transaction")
def reconcile_transaction(
    payload: ReconcileTransactionPayload,
    _: bool = Depends(admin_required),
) -> Dict[str, Any]:
    """Manually reconcile a Paystack transaction when the webhook was missed."""
    ref = payload.reference.strip()
    if not ref:
        raise HTTPException(status_code=400, detail="reference is required")
    txn_data = verify_transaction_reference(ref)
    status_flag = txn_data.get("status")
    if status_flag != "success":
        raise HTTPException(status_code=400, detail="Transaction is not marked successful on Paystack")
    metadata = txn_data.get("metadata", {}) or {}
    api_key_hash = metadata.get("api_key_hash")
    plan_code = metadata.get("plan_code") or metadata.get("plan")
    if not api_key_hash or not plan_code:
        raise HTTPException(
            status_code=400,
            detail="Paystack metadata missing api_key_hash or plan_code; cannot reconcile automatically.",
        )
    existing_txn = find_transaction_by_reference(ref)
    if existing_txn and existing_txn.get("status") == "success":
        record = next((k for k in load_keys().get("keys", []) if k.get("key_hash") == api_key_hash), None)
        updated_txn = update_transaction_by_reference(
            ref,
            subscription_code=existing_txn.get("subscription_code") or txn_data.get("subscription_code"),
            email_token=existing_txn.get("email_token") or txn_data.get("email_token"),
        ) or existing_txn
        return {
            "status": "reconciled",
            "message": "Transaction already reconciled; no changes applied to credits or plan.",
            "key_hash": api_key_hash,
            "plan_code": plan_code,
            "subscription_code": updated_txn.get("subscription_code"),
            "email_token": updated_txn.get("email_token"),
            "key": record,
            "transaction": updated_txn,
        }
    plan = next((p for p in PLANS if p["code"] == plan_code), None)
    if not plan:
        raise HTTPException(status_code=400, detail="Plan from Paystack metadata is not recognised")
    subscription = txn_data.get("subscription", {}) or {}
    subscription_code = (
        txn_data.get("subscription_code")
        or subscription.get("subscription_code")
        or subscription.get("code")
    )
    email_token = txn_data.get("email_token") or subscription.get("email_token")

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
    updated_txn = update_transaction_by_reference(
        ref,
        status="success",
        subscription_code=subscription_code,
        email_token=email_token,
    )
    if not updated_txn:
        update_transactions_for_key(api_key_hash, new_status="success")
    record = next((k for k in load_keys().get("keys", []) if k.get("key_hash") == api_key_hash), None)
    return {
        "status": "reconciled",
        "key_hash": api_key_hash,
        "plan_code": plan_code,
        "subscription_code": subscription_code,
        "email_token": email_token,
        "key": record,
        "transaction": updated_txn or find_transaction_by_reference(ref),
    }


@admin_router.post("/admin/generate-key")
def admin_generate_key(
    payload: OwnerPayload,
    _: bool = Depends(admin_required),
) -> Dict[str, Any]:
    """Admin endpoint to generate a new paid API key."""
    plan = next((p for p in PLANS if p["code"] == payload.plan_code), None) if payload.plan_code else None
    raw = generate_api_key(
        payload.owner,
        email=payload.email,
        first_name=payload.first_name,
        last_name=payload.last_name,
        plan_code=plan["code"] if plan else None,
        active=payload.activate,
        subscription_status="active" if payload.activate else "pending",
    )
    record = next((r for r in load_keys().get("keys", []) if r.get("owner") == payload.owner), None)
    key_hash = record.get("key_hash") if record else None
    if plan and key_hash:
        upgrade_key_tier_by_hash(
            key_hash=key_hash,
            new_tier="paid",
            plan=plan,
            free_monthly_credits=FREE_TIER_MONTHLY_CREDITS,
            subscription_status="active" if payload.activate else "pending",
            active=payload.activate,
        )
    return {
        "owner": payload.owner,
        "api_key": raw,
        "plan_code": plan["code"] if plan else None,
        "contact": {"email": payload.email, "first_name": payload.first_name, "last_name": payload.last_name},
        "active": payload.activate,
    }


@admin_router.post("/admin/revoke-key")
def admin_revoke_key(
    payload: RevokePayload,
    _: bool = Depends(admin_required),
) -> Dict[str, str]:
    """Admin endpoint to revoke an existing API key."""
    revoke_api_key(payload.api_key)
    return {"status": "ok", "message": "API key revoked"}


@admin_router.post("/admin/unrevoke-key")
def admin_unrevoke_key(
    payload: RestorePayload,
    _: bool = Depends(admin_required),
) -> Dict[str, Any]:
    """Admin endpoint to reactivate a previously revoked/inactive API key."""
    record = restore_api_key(payload.api_key, subscription_status=payload.subscription_status)
    return {
        "status": "ok",
        "message": "API key reactivated",
        "active": record.get("active", True),
        "subscription_status": record.get("subscription_status"),
        "tier": record.get("tier"),
    }


@admin_router.get("/admin/usage")
def admin_usage(_: bool = Depends(admin_required)) -> Dict[str, Any]:
    """Return usage summaries for all keys (admin)."""
    from usage_stats import get_usage_map
    usage_map = get_usage_map()
    data = load_keys()
    summaries = []
    for record in data.get("keys", []):
        tracked = usage_map.get(record.get("key_hash"), {}) or {}
        summaries.append(
            {
                "owner": record.get("owner"),
                "email": record.get("email"),
                "tier": record.get("tier"),
                "plan_code": record.get("plan_code"),
                "subscription_status": record.get("subscription_status"),
                "total_requests": tracked.get("total_requests", 0),
                "billable_requests": tracked.get("billable_requests", 0),
                "credits_used": tracked.get("credits_used", 0),
                "first_request_at": to_wat_iso(tracked.get("first_request_at")),
                "last_request_at": to_wat_iso(tracked.get("last_request_at")),
            }
        )
    return {"usage": summaries}


@admin_router.get("/admin/transactions")
def list_all_transactions(_: bool = Depends(admin_required)) -> Dict[str, Any]:
    """Return the transaction history for all API keys (admin only)."""
    txns = load_transactions().get("transactions", [])
    return {"transactions": txns}


@admin_router.get("/admin/keys")
def admin_list_keys(_: bool = Depends(admin_required)) -> Dict[str, Any]:
    """Admin endpoint to list all API keys (with masked hashes)."""
    data = load_keys()
    keys_out = []
    for key in data.get("keys", []):
        keys_out.append(
            {
                "owner": key.get("owner"),
                "active": key.get("active", True),
                "revoked": bool(key.get("revoked_at")),
                "tier": key.get("tier", "paid"),
                "plan_code": key.get("plan_code"),
                "pending_plan_code": key.get("pending_plan_code"),
                "subscription_status": key.get("subscription_status"),
                "email": key.get("email"),
                "first_name": key.get("first_name"),
                "last_name": key.get("last_name"),
                "paystack_subscription_code": key.get("paystack_subscription_code"),
                "created_at": to_wat_iso(key.get("created_at")),
                "upgraded_at": to_wat_iso(key.get("upgraded_at")),
                "revoked_at": to_wat_iso(key.get("revoked_at")),
                "key_prefix": key.get("key_hash", "")[:8] + ".",
            }
        )
    return {"keys": keys_out}
