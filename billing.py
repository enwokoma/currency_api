"""Paystack billing integration for subscription management.

This module encapsulates all communication with the Paystack API for
initialising subscription transactions and verifying webhook calls. It
assumes that your Paystack account has predefined plans corresponding
to the codes defined in the main application. The module also
provides helpers for verifying webhook signatures and extracting
metadata from webhook payloads.
"""

from __future__ import annotations

import json
import os
import hmac
import hashlib
from typing import Tuple, Optional, Any, Dict

import requests
from fastapi import HTTPException


# Paystack configuration: secret key must be set via environment. This
# secret is used both for authenticating API requests and for
# verifying webhook signatures.
def get_paystack_key():
    key = os.getenv("PAYSTACK_SECRET_KEY")
    if not key:
        raise RuntimeError("PAYSTACK_SECRET_KEY is not set in environment")
    return key

## PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "")
PAYSTACK_BASE_URL = "https://api.paystack.co"


def _get_headers() -> Dict[str, str]:
    """Construct headers for Paystack API requests."""
    return {
        "Authorization": f"Bearer {get_paystack_key()}",
        "Content-Type": "application/json",
    }


def initialize_transaction(
    email: str,
    amount_naira: int,
    api_key_hash: str,
    paystack_plan_code: str,
    metadata_plan_code: Optional[str] = None,
) -> Tuple[str, str]:
    """Initiate a Paystack transaction for subscription upgrade.

    Creates a Paystack payment transaction pointing at a specific plan
    defined on your Paystack dashboard. The metadata includes the API
    key hash and your internal ``plan_code`` so that the webhook can
    identify which key to upgrade when payment succeeds.

    Args:
        email: The customer's email address.
        amount_naira: The amount in Naira for the plan (Paystack uses
            kobo internally).
        api_key_hash: Hash of the API key being upgraded (metadata).
        paystack_plan_code: The plan code as defined on Paystack. This
            value is passed directly to the Paystack API's ``plan``
            parameter and must match a plan configured in your Paystack
            dashboard.
        metadata_plan_code: Your internal plan code (e.g. ``BASIC_MONTHLY`` or
            ``PRO_MONTHLY``). If provided, this code is stored in the
            transaction metadata under the ``plan_code`` key. If omitted,
            the Paystack plan code is also used as the metadata plan code.

    Returns:
        A tuple ``(authorization_url, reference)`` where
        ``authorization_url`` is the URL to redirect the user for
        payment and ``reference`` is Paystack's transaction reference.

    Raises:
        HTTPException: If Paystack returns an error.
    """
    # Use the internal plan code for metadata if provided, otherwise fall back
    # to the Paystack plan code. This allows us to map Paystack plans back
    # to our internal plans in the webhook handler.
    meta_code = metadata_plan_code or paystack_plan_code
    payload = {
        "email": email,
        "amount": amount_naira * 100,  # convert Naira to kobo
        # Paystack expects the dashboard plan code here
        "plan": paystack_plan_code,
        "metadata": {
            "api_key_hash": api_key_hash,
            "plan_code": meta_code,
        },
    }
    url = f"{PAYSTACK_BASE_URL}/transaction/initialize"
    resp = requests.post(url, headers=_get_headers(), json=payload, timeout=10)
    try:
        data = resp.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Invalid response from Paystack")
    if not resp.ok or not data.get("status"):
        # Extract message for clarity if present
        detail_msg = data.get("message", "Unknown error")
        raise HTTPException(
            status_code=502,
            detail=f"Paystack init failed: {detail_msg}",
        )
    return data["data"]["authorization_url"], data["data"]["reference"]


def verify_paystack_signature(body: bytes, signature: Optional[str]) -> bool:
    """Verify Paystack webhook signature using SHA‑512.

    Args:
        body: Raw request body from webhook.
        signature: The ``x-paystack-signature`` header value.

    Returns:
        True if the signature matches the computed HMAC, False otherwise.
    """
    if not get_paystack_key() or not signature:
        return False
    computed = hmac.new(
        get_paystack_key().encode("utf-8"), msg=body, digestmod=hashlib.sha512
    ).hexdigest()
    # Normalise case and compare in constant time
    return hmac.compare_digest(computed, signature)


def extract_webhook_info(body: bytes) -> Tuple[str, Optional[str], Optional[str]]:
    """Extract key information from a Paystack webhook payload.

    Args:
        body: Raw request body from the webhook.

    Returns:
        A tuple ``(event_type, api_key_hash, plan_code)``. The
        ``api_key_hash`` and ``plan_code`` values are extracted from
        ``metadata`` if present.

    Raises:
        HTTPException: If the body cannot be parsed.
    """
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid webhook body")
    event_type = data.get("event")
    event_data = data.get("data", {}) or {}
    metadata = event_data.get("metadata", {}) or {}
    api_key_hash = metadata.get("api_key_hash")
    plan_code = metadata.get("plan_code") or metadata.get("plan")
    return event_type, api_key_hash, plan_code