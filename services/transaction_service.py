"""Transaction history management for the currency conversion service.

This module provides helpers for recording and retrieving payment
transactions initiated via Paystack. Each transaction entry is
persisted on disk in a JSON file and includes metadata such as the
API key hash, the plan codes, the transaction reference returned by
Paystack, the amount charged, and status updates from webhook events.

Transactions are stored in ``transactions.json`` in the following
format::

    {
        "transactions": [
            {
                "reference": "ref_abc123",
                "api_key_hash": "...",
                "email": "user@example.com",
                "plan_code": "PRO_MONTHLY",
                "paystack_plan_code": "PLN_xxx",
                "amount_naira": 5000,
                "status": "pending" | "success" | "failed" | "cancelled",
                "subscription_code": "SUB_xxx" | null,
                "email_token": "ETK_xxx" | null,
                "created_at": 1700000000.0,
                "updated_at": 1700000000.0
            },
            ...
        ]
    }

Functions in this module handle loading and saving this JSON file,
adding new transactions, updating the status of existing transactions
based on webhook events, and filtering transactions by API key.

"""

from __future__ import annotations

import time
from typing import Dict, Any, List, Optional

from storage_utils import atomic_write_json, ensure_json_file, load_json_file

AUTHORIZATION_URL_TTL_SECONDS = 2 * 60 * 60  # Paystack authorization URLs expire after 2 hours


# Location of the transaction store. This file is created if it does
# not already exist. It is stored in the same directory as keys.json
# and other data files.
TRANSACTIONS_FILE = "transactions.json"


def _ensure_file() -> None:
    """Ensure the transaction file exists with the proper structure."""
    ensure_json_file(TRANSACTIONS_FILE, "transactions")


def load_transactions() -> Dict[str, Any]:
    """Load the transactions JSON document from disk.

    Returns:
        A dictionary with a single key ``"transactions"`` whose value
        is a list of transaction records.
    """
    _ensure_file()
    return load_json_file(TRANSACTIONS_FILE, "transactions")


def save_transactions(data: Dict[str, Any]) -> None:
    """Persist the transactions JSON document to disk."""
    atomic_write_json(TRANSACTIONS_FILE, data)


def record_transaction(
    api_key_hash: str,
    email: str,
    plan_code: str,
    paystack_plan_code: str,
    amount_naira: int,
    reference: str,
    status: str = "pending",
    subscription_code: Optional[str] = None,
    email_token: Optional[str] = None,
    authorization_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Add a new transaction record to the store.

    Args:
        api_key_hash: The hash of the API key initiating the transaction.
        email: The email used for the transaction.
        plan_code: The internal plan code (e.g. ``BASIC_MONTHLY``).
        paystack_plan_code: The Paystack plan code used for the upgrade.
        amount_naira: The amount in Naira charged for the plan.
        reference: The Paystack transaction reference.
        status: Initial status for the transaction (defaults to ``pending``).
        subscription_code: Optional Paystack subscription code for the transaction.
        email_token: Optional Paystack email token for the transaction.

    Returns:
        The created transaction record.
    """
    data = load_transactions()
    existing = next(
        (t for t in data.get("transactions", []) if t.get("reference") == reference),
        None,
    )
    if existing:
        return existing
    txn = {
        "reference": reference,
        "api_key_hash": api_key_hash,
        "email": email,
        "plan_code": plan_code,
        "paystack_plan_code": paystack_plan_code,
        "amount_naira": amount_naira,
        "status": status,
        "subscription_code": subscription_code,
        "email_token": email_token,
        "authorization_url": authorization_url,
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    data.setdefault("transactions", []).append(txn)
    save_transactions(data)
    return txn


def has_valid_authorization_url(
    txn: Optional[Dict[str, Any]],
    *,
    ttl_seconds: int = AUTHORIZATION_URL_TTL_SECONDS,
) -> bool:
    """Return True if the transaction carries a reusable authorization URL."""
    if not txn or txn.get("status") != "pending":
        return False
    if not txn.get("authorization_url"):
        return False
    created_at = txn.get("created_at") or txn.get("updated_at")
    try:
        created_ts = float(created_at)
    except (TypeError, ValueError):
        return False
    return (time.time() - created_ts) < ttl_seconds


def update_transactions_for_key(
    api_key_hash: str,
    new_status: str,
) -> None:
    """Update the status of all pending transactions for the given key.

    This helper is used when a webhook indicates a successful or
    failed payment. All transactions with status ``pending`` for the
    specified API key hash are updated to ``new_status`` and their
    ``updated_at`` timestamp is refreshed. This assumes that at any
    given time there is at most one pending transaction per key; if
    multiple exist, they will all be updated.

    Args:
        api_key_hash: The API key hash to update transactions for.
        new_status: The status to set (e.g. ``success`` or ``failed``).
    """
    data = load_transactions()
    modified = False
    now = time.time()
    for txn in data.get("transactions", []):
        if txn.get("api_key_hash") == api_key_hash and txn.get("status") == "pending":
            txn["status"] = new_status
            txn["updated_at"] = now
            modified = True
    if modified:
        save_transactions(data)


def get_transactions_for_key(api_key_hash: str) -> List[Dict[str, Any]]:
    """Retrieve a list of transactions for the specified API key.

    Args:
        api_key_hash: The hash of the API key whose transactions should be returned.

    Returns:
        A list of transaction dictionaries filtered by the key.
    """
    data = load_transactions()
    return [t for t in data.get("transactions", []) if t.get("api_key_hash") == api_key_hash]


def get_pending_transaction_for_key(api_key_hash: str) -> Optional[Dict[str, Any]]:
    """Return the latest pending transaction for a key, if any."""
    txns = [
        t
        for t in load_transactions().get("transactions", [])
        if t.get("api_key_hash") == api_key_hash and t.get("status") == "pending"
    ]
    if not txns:
        return None
    # Return the most recent pending transaction
    txns.sort(key=lambda t: t.get("created_at", 0), reverse=True)
    return txns[0]


def get_latest_transaction_for_key(api_key_hash: str) -> Optional[Dict[str, Any]]:
    """Return the most recent transaction (any status) for a key."""
    txns = [
        t
        for t in load_transactions().get("transactions", [])
        if t.get("api_key_hash") == api_key_hash
    ]
    if not txns:
        return None
    txns.sort(key=lambda t: t.get("created_at", 0), reverse=True)
    return txns[0]


def find_transaction_by_reference(reference: str) -> Optional[Dict[str, Any]]:
    """Return a transaction by its Paystack reference."""
    for txn in load_transactions().get("transactions", []):
        if txn.get("reference") == reference:
            return txn
    return None


def update_transaction_by_reference(
    reference: str,
    *,
    status: Optional[str] = None,
    subscription_code: Optional[str] = None,
    email_token: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Update a transaction identified by reference and persist it."""
    data = load_transactions()
    updated = None
    for txn in data.get("transactions", []):
        if txn.get("reference") != reference:
            continue
        if status is not None:
            txn["status"] = status
        if subscription_code is not None:
            txn["subscription_code"] = subscription_code
        if email_token is not None:
            txn["email_token"] = email_token
        txn["updated_at"] = time.time()
        updated = txn
        break
    if updated:
        save_transactions(data)
    return updated
