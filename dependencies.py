"""Shared FastAPI dependencies for authentication and rate limiting."""

from __future__ import annotations

import os
import time
from typing import Dict, Any

from fastapi import Depends, HTTPException, Request

from admin_utils import get_expected_admin_token
from services.auth_service import (
    validate_api_key,
    charge_call_for_key,
)
from billing_helpers import _rate_limits_for_record
from config import (
    PLANS,
    FREE_TIER_MONTHLY_CREDITS,
)
from rate_limit import allow_request
from usage_stats import record_usage_event
from services.user_service import get_user_by_session


def api_key_required(request: Request) -> Dict[str, Any]:
    """Resolve the calling key, enforce rate limits and charge credits."""
    raw_key = request.headers.get("X-API-KEY") or request.query_params.get("api_key")
    key_record = validate_api_key(raw_key)
    tier = key_record.get("tier", "paid")
    per_minute, per_day = _rate_limits_for_record(key_record)
    # Enforce rate limits
    allow_request(
        key_hash=key_record["key_hash"],
        per_minute=per_minute,
        per_day=per_day,
    )
    # Charge credits
    updated_record, billed = charge_call_for_key(
        key_record=key_record,
        free_monthly_credits=FREE_TIER_MONTHLY_CREDITS,
        plans=PLANS,
    )
    record_usage_event(
        key_hash=updated_record.get("key_hash"),
        owner=updated_record.get("owner"),
        email=updated_record.get("email"),
        first_name=updated_record.get("first_name"),
        last_name=updated_record.get("last_name"),
        tier=updated_record.get("tier", tier),
        plan_code=updated_record.get("plan_code"),
        subscription_status=updated_record.get("subscription_status"),
        billed=billed,
        remaining_credits=updated_record.get("credits"),
        credits_reset_at=updated_record.get("credits_reset_at"),
    )
    key_record.update(
        {
            "plan_code": updated_record.get("plan_code"),
            "pending_plan_code": updated_record.get("pending_plan_code"),
            "subscription_status": updated_record.get("subscription_status"),
            "credits": updated_record.get("credits"),
            "credits_reset_at": updated_record.get("credits_reset_at"),
        }
    )
    return key_record


def paid_key_required(request: Request) -> Dict[str, Any]:
    """Enforce that the caller is using a paid key (for premium endpoints)."""
    key_record = api_key_required(request)
    if key_record.get("tier") != "paid":
        raise HTTPException(
            status_code=403,
            detail="This endpoint is available to paid subscriptions only.",
        )
    return key_record


def session_required(request: Request) -> Dict[str, Any]:
    """Resolve the calling user based on a session token."""
    token = request.headers.get("X-SESSION-TOKEN") or request.query_params.get("session_token")
    user = get_user_by_session(token)
    return user


def admin_required(request: Request) -> bool:
    """Validate the admin token for protected admin endpoints."""
    provided = request.headers.get("X-ADMIN-TOKEN") or request.query_params.get("admin_token")
    expected = get_expected_admin_token()
    if provided != expected:
        raise HTTPException(status_code=403, detail="Invalid admin token")
    return True
