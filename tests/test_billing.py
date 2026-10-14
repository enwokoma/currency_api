"""Paystack billing: plans, subscribe, upgrade, webhooks, verify, cancel, transactions."""

from __future__ import annotations

import time

import helpers
from helpers import (
    ADMIN,
    activate_via_webhook,
    free_key,
    hash_key,
    key_record,
    patch_key,
    patch_transaction,
    rate_call,
    read_store,
    send_webhook,
    subscribe,
    transactions_for,
    upgrade,
)


def test_plans_are_public(client):
    r = client.get("/plans")
    assert r.status_code == 200
    plans = {p["code"]: p for p in r.json()["plans"]}
    assert plans["BASIC_MONTHLY"]["paystack_plan_code"] == "PLN_basic"
    assert plans["PRO_MONTHLY"]["paystack_plan_code"] == "PLN_pro"
    assert plans["PRO_MONTHLY"]["monthly_credits"] == 20000


def test_subscribe_creates_pending_key_and_checkout(client, paystack):
    r = subscribe(client, " Bill@Example.com", owner="acct-42")
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == "paid" and body["plan"]["code"] == "BASIC_MONTHLY"
    assert body["authorization_url"] == "https://checkout.paystack.com/ref_1" and body["reference"] == "ref_1"
    key = body["api_key"]

    rec = key_record(key)
    assert rec["active"] is False and rec["subscription_status"] == "pending"
    assert rec["pending_plan_code"] == "BASIC_MONTHLY" and rec["plan_code"] is None
    assert rec["email"] == "bill@example.com" and rec["owner"] == "acct-42"

    txn = transactions_for(key)[0]
    assert txn["status"] == "pending" and txn["amount_naira"] == 2000
    assert txn["paystack_plan_code"] == "PLN_basic" and txn["authorization_url"] == body["authorization_url"]

    init = paystack.initialized["ref_1"]
    assert init["email"] == "bill@example.com" and init["amount"] == 200000 and init["plan"] == "PLN_basic"
    assert init["metadata"]["api_key_hash"] == hash_key(key) and init["metadata"]["plan_code"] == "BASIC_MONTHLY"
    assert init["metadata"]["first_name"] == "Bill"

    assert rate_call(client, key).status_code == 402, "inactive until Paystack confirms"
    assert subscribe(client, "bill@example.com").status_code == 400
    assert subscribe(client, "other@example.com", plan_code="GOLD").status_code == 400


def test_subscribe_reports_paystack_failure(client, paystack):
    paystack.init_fail = True
    r = subscribe(client, "fail@example.com")
    assert r.status_code == 502 and "Paystack init failed" in r.json()["detail"]


def test_subscribe_rolls_back_the_key_when_checkout_fails(client, paystack):
    paystack.init_fail = True
    assert subscribe(client, "retry@example.com").status_code == 502
    assert read_store("keys.json")["keys"] == [], "the never-issued key must not linger and reserve the email"
    assert read_store("transactions.json")["transactions"] == []

    paystack.init_fail = False
    r = subscribe(client, "retry@example.com")
    assert r.status_code == 200
    assert key_record(r.json()["api_key"])["subscription_status"] == "pending"


def test_subscribe_requires_configured_paystack_plan_code(client, monkeypatch):
    monkeypatch.setitem(helpers.plan("BASIC_MONTHLY"), "paystack_plan_code", None)
    r = subscribe(client, "nocode@example.com")
    assert r.status_code == 400 and "not configured" in r.json()["detail"]


def test_upgrade_free_key_full_flow(client, paystack):
    key = free_key(client, "up@example.com")
    r = upgrade(client, key, "PRO_MONTHLY")
    assert r.status_code == 200
    assert r.json()["reference"] == "ref_1" and r.json()["plan"]["code"] == "PRO_MONTHLY"
    rec = key_record(key)
    assert rec["pending_plan_code"] == "PRO_MONTHLY" and rec["subscription_status"] == "pending" and rec["active"] is True
    assert rate_call(client, key).status_code == 200, "a free key stays usable while its upgrade is pending"

    r = upgrade(client, key, "PRO_MONTHLY")
    assert r.status_code == 200 and r.json()["reference"] == "ref_1"
    assert "Continue the existing pending payment" in r.json()["message"]

    patch_transaction("ref_1", created_at=time.time() - 3 * 3600)
    r = upgrade(client, key, "PRO_MONTHLY")
    assert r.status_code == 200 and r.json()["reference"] == "ref_2"
    assert "expired or is missing" in r.json()["message"]

    rec = activate_via_webhook(client, paystack, key, "PRO_MONTHLY", "ref_2")
    assert rec["tier"] == "paid" and rec["plan_code"] == "PRO_MONTHLY" and rec["pending_plan_code"] is None
    assert rec["subscription_status"] == "active" and rec["active"] is True
    assert rec["credits"] == 20000 and isinstance(rec["upgraded_at"], float)
    assert rec["paystack_subscription_code"] == "SUB_pro" and rec["paystack_email_token"] == "ETK_pro"
    txns = {t["reference"]: t for t in transactions_for(key)}
    assert txns["ref_2"]["status"] == "success" and txns["ref_2"]["subscription_code"] == "SUB_pro"

    r = upgrade(client, key, "PRO_MONTHLY")
    assert r.status_code == 400 and "already has this active plan" in r.json()["detail"]
    r = upgrade(client, key, "BASIC_MONTHLY")
    assert r.status_code == 200 and key_record(key)["pending_plan_code"] == "BASIC_MONTHLY"


def test_upgrade_updates_contact_and_keeps_emails_unique(client):
    key1 = free_key(client, "one@example.com")
    free_key(client, "two@example.com")
    r = upgrade(client, key1, "BASIC_MONTHLY", email="two@example.com", first_name="A", last_name="B")
    assert r.status_code == 400 and "already associated" in r.json()["detail"]
    r = upgrade(client, key1, "BASIC_MONTHLY", email="Three@Example.com", first_name="Tre", last_name="Uno")
    assert r.status_code == 200
    rec = key_record(key1)
    assert rec["email"] == "three@example.com" and rec["first_name"] == "Tre"


def test_upgrade_by_key_prefix_needs_an_owning_session(client):
    key = free_key(client, "prefix@example.com")
    prefix = hash_key(key)[:8]
    r = client.post("/billing/upgrade", headers={"X-API-KEY-PREFIX": prefix}, json={"plan_code": "BASIC_MONTHLY"})
    assert r.status_code == 401 and "prefix alone" in r.json()["detail"]
    assert key_record(key)["pending_plan_code"] is None
    assert client.post("/billing/upgrade", json={"plan_code": "BASIC_MONTHLY"}).status_code == 401


def test_upgrade_requires_contact_names(client):
    key = free_key(client, "legacy@example.com")
    patch_key(key, first_name=None, last_name=None)
    r = upgrade(client, key, "BASIC_MONTHLY")
    assert r.status_code == 400 and "required" in r.json()["detail"]
    assert upgrade(client, key, "BASIC_MONTHLY", first_name="L", last_name="K").status_code == 200


def test_upgrade_rejects_revoked_keys_and_unknown_plans(client):
    key = free_key(client, "rev@example.com")
    assert upgrade(client, key, "PLATINUM").status_code == 400
    client.post("/admin/revoke-key", headers=ADMIN, json={"api_key": key})
    r = upgrade(client, key, "BASIC_MONTHLY")
    assert r.status_code == 403 and "inactive" in r.json()["detail"]


def test_upgrade_recovers_after_paystack_failure(client, paystack):
    key = free_key(client, "flaky@example.com")
    paystack.init_fail = True
    assert upgrade(client, key, "BASIC_MONTHLY").status_code == 502
    assert key_record(key)["pending_plan_code"] == "BASIC_MONTHLY" and transactions_for(key) == []
    paystack.init_fail = False
    r = upgrade(client, key, "BASIC_MONTHLY")
    assert r.status_code == 200 and "missing" in r.json()["message"] and r.json()["reference"] == "ref_1"


def test_webhook_signature_and_body_validation(client):
    assert send_webhook(client, "charge.success", {}, signature=None).status_code == 400
    assert send_webhook(client, "charge.success", {}, signature="deadbeef").status_code == 400
    body = b"not json"
    r = client.post("/paystack/webhook", content=body, headers={"x-paystack-signature": helpers.sign(body)})
    assert r.status_code == 400 and "Invalid webhook body" in r.json()["detail"]
    r = send_webhook(client, "charge.success", {"metadata": {"api_key_hash": "x", "plan_code": "BASIC_MONTHLY"}})
    assert r.status_code == 400 and "Missing Paystack reference" in r.json()["detail"]


def test_webhook_charge_success_activates_and_is_idempotent(client, paystack):
    key = subscribe(client, "hook@example.com").json()["api_key"]
    rec = activate_via_webhook(client, paystack, key, "BASIC_MONTHLY", "ref_1")
    assert rec["active"] is True and rec["plan_code"] == "BASIC_MONTHLY" and rec["credits"] == 5000
    assert rec["paystack_subscription_code"] == "SUB_basic" and rec["paystack_email_token"] == "ETK_basic"
    txn = transactions_for(key)[0]
    assert txn["status"] == "success" and txn["subscription_code"] == "SUB_basic" and txn["email_token"] == "ETK_basic"
    assert any("/subscription?customer=1" in url for _, url, _ in paystack.calls)

    assert rate_call(client, key).status_code == 200
    assert key_record(key)["credits"] == 4999

    hashed = hash_key(key)
    r = send_webhook(client, "charge.success", {"reference": "ref_1", "metadata": {"api_key_hash": hashed, "plan_code": "BASIC_MONTHLY"}})
    assert r.status_code == 200
    assert key_record(key)["credits"] == 4999, "a replayed webhook must not reset credits"


def test_webhook_rejects_metadata_mismatch(client, paystack):
    key = subscribe(client, "mm@example.com").json()["api_key"]
    paystack.mark_success("ref_1", "someone-else", "BASIC_MONTHLY")
    r = send_webhook(client, "charge.success", {"reference": "ref_1", "metadata": {"api_key_hash": hash_key(key), "plan_code": "BASIC_MONTHLY"}})
    assert r.status_code == 400 and "does not match API key" in r.json()["detail"]
    assert key_record(key)["active"] is False


def test_webhook_ignores_unknown_events_and_unknown_keys(client, paystack):
    key = subscribe(client, "unk@example.com").json()["api_key"]
    assert send_webhook(client, "transfer.success", {"reference": "t1"}).status_code == 200
    paystack.mark_success("ref_9", "deadbeef", "BASIC_MONTHLY")
    r = send_webhook(client, "charge.success", {"reference": "ref_9", "metadata": {"api_key_hash": "deadbeef", "plan_code": "BASIC_MONTHLY"}})
    assert r.status_code == 200
    assert key_record(key)["active"] is False and len(read_store("transactions.json")["transactions"]) == 1


def test_webhook_never_reactivates_a_revoked_key(client, paystack):
    key = subscribe(client, "revhook@example.com").json()["api_key"]
    assert client.post("/admin/revoke-key", headers=ADMIN, json={"api_key": key}).status_code == 200
    rec = activate_via_webhook(client, paystack, key, "BASIC_MONTHLY", "ref_1")
    assert rec["active"] is False and rec["revoked_at"] and rec["plan_code"] is None
    assert transactions_for(key)[0]["status"] == "failed"
    assert rate_call(client, key).status_code == 403


def test_webhook_subscription_disable_downgrades_to_free(client, paystack):
    key = subscribe(client, "dis@example.com").json()["api_key"]
    activate_via_webhook(client, paystack, key, "BASIC_MONTHLY", "ref_1")
    assert rate_call(client, key).status_code == 200
    hashed = hash_key(key)

    r = send_webhook(client, "subscription.disable", {"metadata": {"api_key_hash": hashed, "plan_code": "BASIC_MONTHLY"}, "subscription_code": "SUB_basic", "email_token": "ETK_basic"})
    assert r.status_code == 200
    rec = key_record(key)
    assert rec["tier"] == "free" and rec["subscription_status"] == "canceled" and rec["plan_code"] is None
    assert rec["credits"] == 200 and rec["paystack_subscription_code"] is None and rec["active"] is True
    assert transactions_for(key)[0]["status"] == "success", "the paid transaction history is kept"

    assert rate_call(client, key).status_code == 200
    assert key_record(key)["credits"] == 199
    r = send_webhook(client, "subscription.disable", {"metadata": {"api_key_hash": hashed}})
    assert r.status_code == 200 and key_record(key)["credits"] == 199, "a repeat disable on a free key changes nothing"


def test_webhook_invoice_failed_marks_transaction_and_downgrades(client, paystack):
    key = free_key(client, "inv@example.com")
    upgrade(client, key, "BASIC_MONTHLY")
    activate_via_webhook(client, paystack, key, "BASIC_MONTHLY", "ref_1")
    upgrade(client, key, "PRO_MONTHLY")  # pending ref_2
    hashed = hash_key(key)
    r = send_webhook(client, "invoice.failed", {"reference": "ref_2", "metadata": {"api_key_hash": hashed, "plan_code": "PRO_MONTHLY"}})
    assert r.status_code == 200
    txns = {t["reference"]: t["status"] for t in transactions_for(key)}
    assert txns == {"ref_1": "success", "ref_2": "failed"}
    rec = key_record(key)
    assert rec["tier"] == "free" and rec["subscription_status"] == "canceled"


def test_verify_endpoint_applies_upgrade(client, paystack):
    key = free_key(client, "ver@example.com")
    ref = upgrade(client, key, "BASIC_MONTHLY").json()["reference"]
    hashed = hash_key(key)

    r = client.post("/billing/verify", headers={"X-API-KEY": key}, json={"reference": ref})
    assert r.status_code == 400 and "verification failed" in r.json()["detail"], "unknown to Paystack"
    paystack.mark_success(ref, hashed, "BASIC_MONTHLY", status="abandoned")
    r = client.post("/billing/verify", headers={"X-API-KEY": key}, json={"reference": ref})
    assert r.status_code == 400 and "not marked successful" in r.json()["detail"]
    paystack.mark_success(ref, hashed, "PRO_MONTHLY")
    r = client.post("/billing/verify", headers={"X-API-KEY": key}, json={"reference": ref})
    assert r.status_code == 400 and "does not match plan" in r.json()["detail"]

    paystack.mark_success(ref, hashed, "BASIC_MONTHLY")
    r = client.post("/billing/verify", headers={"X-API-KEY": key}, json={"reference": ref})
    assert r.status_code == 200 and r.json()["status"] == "verified" and r.json()["plan_code"] == "BASIC_MONTHLY"
    rec = key_record(key)
    assert rec["active"] and rec["plan_code"] == "BASIC_MONTHLY" and rec["credits"] == 5000
    assert rec["paystack_subscription_code"] == "SUB_basic"
    assert r.json()["transaction"]["status"] == "success"

    r = client.post("/billing/verify", headers={"X-API-KEY": key}, json={"reference": ref})
    assert r.status_code == 200 and "already verified" in r.json()["message"]

    other = free_key(client, "other@example.com")
    r = client.post("/billing/verify", headers={"X-API-KEY": other}, json={"reference": ref})
    assert r.status_code == 403
    assert client.post("/billing/verify", headers={"X-API-KEY": key}, json={"reference": "  "}).status_code == 400


def test_cancel_subscription(client, paystack):
    free = free_key(client, "nosub@example.com")
    r = client.post("/billing/cancel", headers={"X-API-KEY": free})
    assert r.status_code == 400 and "No active Paystack subscription" in r.json()["detail"]

    key = subscribe(client, "cancel@example.com").json()["api_key"]
    activate_via_webhook(client, paystack, key, "BASIC_MONTHLY", "ref_1")
    upgrade(client, key, "PRO_MONTHLY")  # leaves a pending ref_2

    paystack.disable_fail = True
    r = client.post("/billing/cancel", headers={"X-API-KEY": key})
    assert r.status_code == 502 and key_record(key)["tier"] == "paid"

    paystack.disable_fail = False
    r = client.post("/billing/cancel", headers={"X-API-KEY": key})
    assert r.status_code == 200
    assert paystack.disabled[-1] == {"code": "SUB_basic", "token": "ETK_basic"}
    rec = key_record(key)
    assert rec["tier"] == "free" and rec["subscription_status"] == "canceled" and rec["credits"] == 200
    txns = {t["reference"]: t["status"] for t in transactions_for(key)}
    assert txns == {"ref_1": "success", "ref_2": "cancelled"}
    assert rate_call(client, key).status_code == 200


def test_transactions_listing_per_key(client):
    key1 = free_key(client, "t1@example.com")
    key2 = free_key(client, "t2@example.com")
    upgrade(client, key1, "BASIC_MONTHLY")
    upgrade(client, key2, "PRO_MONTHLY")
    r = client.get("/transactions", headers={"X-API-KEY": key1})
    assert r.status_code == 200
    assert [t["reference"] for t in r.json()["transactions"]] == ["ref_1"]
    assert r.json()["transactions"][0]["api_key_hash"] == hash_key(key1)


def test_pending_key_can_poll_its_transactions_without_being_charged(client, paystack):
    key = subscribe(client, "pendtx@example.com").json()["api_key"]
    r = client.get("/transactions", headers={"X-API-KEY": key})
    assert r.status_code == 200
    assert [t["status"] for t in r.json()["transactions"]] == ["pending"]

    activate_via_webhook(client, paystack, key, "BASIC_MONTHLY", "ref_1")
    before = key_record(key)["credits"]
    assert client.get("/transactions", headers={"X-API-KEY": key}).json()["transactions"][0]["status"] == "success"
    assert key_record(key)["credits"] == before, "reading history is not a billable call"

    client.post("/admin/revoke-key", headers=ADMIN, json={"api_key": key})
    assert client.get("/transactions", headers={"X-API-KEY": key}).status_code == 403
    assert client.get("/transactions").status_code == 401
