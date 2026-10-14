"""Bootstrap and helpers shared by the end-to-end tests.

Importing this module (conftest does it first) moves the process into a
scratch directory and pins the environment BEFORE any application module is
imported. The app resolves every JSON store relative to the working directory
and reads the admin token and Paystack plan codes from the environment at
import time, so both must be in place first.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import tempfile
from typing import Any, Dict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

DATA_DIR = tempfile.mkdtemp(prefix="currency_api_tests_")
os.chdir(DATA_DIR)

ADMIN_TOKEN = "test-admin"
PAYSTACK_SECRET = "test-secret"
os.environ["ADMIN_TOKEN"] = ADMIN_TOKEN
os.environ["PAYSTACK_SECRET_KEY"] = PAYSTACK_SECRET
os.environ["PAYSTACK_PLAN_BASIC"] = "PLN_basic"
os.environ["PAYSTACK_PLAN_PRO"] = "PLN_pro"

import main  # noqa: E402  (must come after the environment is pinned)
from config import PLANS  # noqa: E402
from services.auth_service import hash_key  # noqa: E402
from storage_utils import atomic_write_json  # noqa: E402

ADMIN = {"X-ADMIN-TOKEN": ADMIN_TOKEN}
# Every email the app sends during a test (conftest routes mailer delivery here).
OUTBOX: list = []
PASSWORD = "StrongPass123"
STORES = {
    "keys.json": "keys",
    "users.json": "users",
    "sessions.json": "sessions",
    "transactions.json": "transactions",
    "usage.json": "usage",
}


# --------------------------------------------------------------------------- stores
def read_store(name: str) -> Dict[str, Any]:
    with open(name, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_store(name: str, data: Dict[str, Any]) -> None:
    atomic_write_json(name, data)


def key_record(raw_key: str) -> Dict[str, Any]:
    hashed = hash_key(raw_key)
    return next(r for r in read_store("keys.json")["keys"] if r["key_hash"] == hashed)


def patch_key(raw_key: str, **fields: Any) -> Dict[str, Any]:
    data = read_store("keys.json")
    hashed = hash_key(raw_key)
    for record in data["keys"]:
        if record["key_hash"] == hashed:
            record.update(fields)
            write_store("keys.json", data)
            return record
    raise AssertionError("key not found")


def transactions_for(raw_key: str):
    hashed = hash_key(raw_key)
    return [t for t in read_store("transactions.json")["transactions"] if t["api_key_hash"] == hashed]


def patch_transaction(reference: str, **fields: Any) -> None:
    data = read_store("transactions.json")
    for txn in data["transactions"]:
        if txn["reference"] == reference:
            txn.update(fields)
            write_store("transactions.json", data)
            return
    raise AssertionError("transaction not found")


def relax_public_limits(monkeypatch) -> None:
    """Lift the per-client limits for tests that call public routes many times."""
    import config

    for route in list(config.PUBLIC_ROUTE_LIMITS):
        monkeypatch.setitem(config.PUBLIC_ROUTE_LIMITS, route, (1000, 10000))


# --------------------------------------------------------------------------- mail
def emails_to(address: str) -> list:
    return [m for m in OUTBOX if m["To"] == address.strip().lower()]


def last_email_to(address: str):
    sent = emails_to(address)
    assert sent, f"no email was sent to {address}"
    return sent[-1]


# --------------------------------------------------------------------------- users
def register(client, email: str, first="Ada", last="Lovelace", password=PASSWORD, confirm=None):
    return client.post(
        "/auth/register",
        json={
            "first_name": first,
            "last_name": last,
            "email": email,
            "password": password,
            "confirm_password": password if confirm is None else confirm,
        },
    )


def verification_for(email: str) -> Dict[str, str]:
    """The latest verification code and token, read from the email the user got."""
    body = last_email_to(email).get_content()
    return {
        "otp": re.search(r"^Verification code: (\d{6})$", body, re.M).group(1),
        "token": re.search(r"^Verification token: (\S+)$", body, re.M).group(1),
    }


def register_and_login(client, email: str, **kwargs) -> Dict[str, str]:
    r = register(client, email, **kwargs)
    assert r.status_code == 202, r.text
    otp = verification_for(email)["otp"]
    r = client.post("/auth/verify-email", json={"email": email, "otp": otp})
    assert r.status_code == 200, r.text
    r = client.post("/auth/login", json={"email": email, "password": kwargs.get("password", PASSWORD)})
    assert r.status_code == 200, r.text
    return {"X-SESSION-TOKEN": r.json()["session_token"]}


# --------------------------------------------------------------------------- keys
def api_key_from_email(email: str) -> str:
    """The API key in the latest email sent to ``email``."""
    body = last_email_to(email).get_content()
    return re.search(r"^API key: (\S+)$", body, re.M).group(1)


def free_key(client, email: str, first="Free", last="User") -> str:
    r = client.post("/auth/free-key", json={"email": email, "first_name": first, "last_name": last})
    assert r.status_code == 202, r.text
    return api_key_from_email(email)


def admin_key(client, owner: str, email: str, plan_code=None, activate=True, first="Admin", last="User") -> str:
    r = client.post(
        "/admin/generate-key",
        headers=ADMIN,
        json={
            "owner": owner,
            "email": email,
            "first_name": first,
            "last_name": last,
            "plan_code": plan_code,
            "activate": activate,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["api_key"]


def rate_call(client, key: str, frm="USD", to="NGN"):
    return client.post("/rate", headers={"X-API-KEY": key}, json={"from_currency": frm, "to_currency": to})


def usage(client, key: str) -> Dict[str, Any]:
    r = client.get("/usage", headers={"X-API-KEY": key})
    assert r.status_code == 200, r.text
    return r.json()["usage"]


# --------------------------------------------------------------------------- billing
def subscribe(client, email: str, plan_code="BASIC_MONTHLY", first="Bill", last="Able", owner=None):
    payload = {"plan_code": plan_code, "email": email, "first_name": first, "last_name": last}
    if owner is not None:
        payload["owner"] = owner
    return client.post("/billing/subscribe", json=payload)


def upgrade(client, key: str, plan_code: str, **contact):
    return client.post("/billing/upgrade", headers={"X-API-KEY": key}, json={"plan_code": plan_code, **contact})


def sign(body: bytes) -> str:
    return hmac.new(PAYSTACK_SECRET.encode("utf-8"), body, hashlib.sha512).hexdigest()


def send_webhook(client, event: str, data: Dict[str, Any], signature=None):
    body = json.dumps({"event": event, "data": data}).encode("utf-8")
    headers = {"content-type": "application/json"}
    sig = sign(body) if signature is None else signature
    if sig is not None:
        headers["x-paystack-signature"] = sig
    return client.post("/paystack/webhook", content=body, headers=headers)


def activate_via_webhook(client, paystack, raw_key: str, plan_code: str, reference: str) -> Dict[str, Any]:
    """Drive a key from pending to active the way a real Paystack charge would."""
    hashed = hash_key(raw_key)
    paystack.mark_success(reference, hashed, plan_code)
    r = send_webhook(
        client,
        "charge.success",
        {"reference": reference, "metadata": {"api_key_hash": hashed, "plan_code": plan_code}},
    )
    assert r.status_code == 200, r.text
    return key_record(raw_key)


def plan(code: str) -> Dict[str, Any]:
    return next(p for p in PLANS if p["code"] == code)
