"""Admin endpoints: auth, key issuance, revoke/restore, listings, reconciliation."""

from __future__ import annotations

import helpers
from helpers import (
    ADMIN,
    activate_via_webhook,
    admin_key,
    free_key,
    hash_key,
    key_record,
    rate_call,
    register_and_login,
    subscribe,
    transactions_for,
)


def test_admin_token_is_required(client):
    assert client.get("/admin/keys").status_code == 403
    assert client.get("/admin/keys", headers={"X-ADMIN-TOKEN": "wrong"}).status_code == 403
    assert client.get("/admin/keys", headers=ADMIN).status_code == 200
    assert client.get("/admin/keys", params={"admin_token": helpers.ADMIN_TOKEN}).status_code == 200


def test_generate_key_with_plan_is_immediately_usable(client):
    r = client.post("/admin/generate-key", headers=ADMIN, json={"owner": "acme", "email": "acme@example.com", "first_name": "Ac", "last_name": "Me", "plan_code": "BASIC_MONTHLY"})
    assert r.status_code == 200
    body = r.json()
    assert body["plan_code"] == "BASIC_MONTHLY" and body["active"] is True and body["contact"]["email"] == "acme@example.com"
    rec = key_record(body["api_key"])
    assert rec["tier"] == "paid" and rec["credits"] == 5000 and rec["subscription_status"] == "active"
    assert rate_call(client, body["api_key"]).status_code == 200
    r = client.post("/rates/historical", headers={"X-API-KEY": body["api_key"]}, json={"base_currency": "USD", "date": "2024-01-02"})
    assert r.status_code == 200

    r = client.post("/admin/generate-key", headers=ADMIN, json={"owner": "acme", "email": "acme@example.com", "first_name": "Ac", "last_name": "Me"})
    assert r.status_code == 400, "email uniqueness applies to admin keys too"


def test_generate_key_rejects_unknown_plan_code(client):
    payload = {"owner": "typo", "email": "typo@example.com", "first_name": "T", "last_name": "Y", "plan_code": "PRO_MONTLY"}
    r = client.post("/admin/generate-key", headers=ADMIN, json=payload)
    assert r.status_code == 400 and "Unknown plan_code" in r.json()["detail"]
    assert helpers.read_store("keys.json")["keys"] == [], "a rejected request must not leave a plan-less key behind"
    payload["plan_code"] = None
    assert client.post("/admin/generate-key", headers=ADMIN, json=payload).status_code == 200, "omitting the plan is still allowed"


def test_generate_inactive_key_stays_pending(client):
    key = admin_key(client, "later", "later@example.com", plan_code="PRO_MONTHLY", activate=False)
    rec = key_record(key)
    assert rec["active"] is False and rec["subscription_status"] == "pending"
    assert rate_call(client, key).status_code == 402


def test_generate_key_for_same_owner_twice_applies_plan_to_the_new_key(client):
    first = admin_key(client, "dup", "dup1@example.com", plan_code="BASIC_MONTHLY")
    second = admin_key(client, "dup", "dup2@example.com", plan_code="PRO_MONTHLY")
    assert key_record(first)["plan_code"] == "BASIC_MONTHLY" and key_record(first)["credits"] == 5000
    assert key_record(second)["plan_code"] == "PRO_MONTHLY" and key_record(second)["credits"] == 20000


def test_revoke_and_unrevoke(client):
    key = free_key(client, "rv@example.com")
    assert client.post("/admin/revoke-key", headers=ADMIN, json={"api_key": key}).status_code == 200
    assert rate_call(client, key).status_code == 403
    assert client.post("/admin/revoke-key", headers=ADMIN, json={"api_key": "free_missing"}).status_code == 404

    r = client.post("/admin/unrevoke-key", headers=ADMIN, json={"api_key": key})
    assert r.status_code == 200 and r.json()["active"] is True
    assert rate_call(client, key).status_code == 200
    assert "revoked_at" not in key_record(key)

    paid = admin_key(client, "p", "p@example.com", plan_code="PRO_MONTHLY")
    client.post("/admin/revoke-key", headers=ADMIN, json={"api_key": paid})
    client.post("/admin/unrevoke-key", headers=ADMIN, json={"api_key": paid})
    assert rate_call(client, paid).status_code == 402, "a paid key restored without a status keeps status=revoked"
    r = client.post("/admin/unrevoke-key", headers=ADMIN, json={"api_key": paid, "subscription_status": "active"})
    assert r.status_code == 200 and r.json()["subscription_status"] == "active"
    assert rate_call(client, paid).status_code == 200


def test_admin_listings_and_pagination(client):
    free_key(client, "l1@example.com")
    admin_key(client, "l2", "l2@example.com", plan_code="BASIC_MONTHLY")
    admin_key(client, "l3", "l3@example.com")
    register_and_login(client, "u1@example.com")

    r = client.get("/admin/keys", headers=ADMIN, params={"page": 2, "page_size": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3 and body["page"] == 2 and len(body["items"]) == 1
    items = client.get("/admin/keys", headers=ADMIN).json()["items"]
    by_email = {i["email"]: i for i in items}
    assert by_email["l1@example.com"]["credits_purchased"] == 200
    assert by_email["l2@example.com"]["credits_purchased"] == 5000
    assert by_email["l3@example.com"]["credits_purchased"] is None
    assert all(len(i["key_prefix"]) == 9 and isinstance(i["created_at"], str) for i in items)
    assert not any("key_hash" in i for i in items), "hashes are never exposed"

    assert client.get("/admin/keys", headers=ADMIN, params={"page_size": 201}).status_code == 422
    assert client.get("/admin/keys", headers=ADMIN, params={"page": 0}).status_code == 422

    users = client.get("/admin/users", headers=ADMIN).json()
    assert users["total"] == 1 and users["items"][0]["email"] == "u1@example.com"
    assert "password_hash" not in users["items"][0]


def test_admin_usage_report(client):
    key = admin_key(client, "usage", "usage@example.com", plan_code="BASIC_MONTHLY")
    for _ in range(4):
        rate_call(client, key)
    items = client.get("/admin/usage", headers=ADMIN).json()["items"]
    entry = next(i for i in items if i["email"] == "usage@example.com")
    assert entry["total_requests"] == 4 and entry["billable_requests"] == 4 and entry["credits_used"] == 4
    assert entry["plan_code"] == "BASIC_MONTHLY" and isinstance(entry["last_request_at"], str)


def test_reconcile_transaction_after_missed_webhook(client, paystack):
    key = subscribe(client, "recon@example.com").json()["api_key"]
    hashed = hash_key(key)

    assert client.post("/admin/reconcile-transaction", headers=ADMIN, json={"reference": " "}).status_code == 400
    r = client.post("/admin/reconcile-transaction", headers=ADMIN, json={"reference": "ref_1"})
    assert r.status_code == 400, "Paystack does not know the reference yet"
    paystack.mark_success("ref_1", hashed, "BASIC_MONTHLY", status="failed")
    assert "not marked successful" in client.post("/admin/reconcile-transaction", headers=ADMIN, json={"reference": "ref_1"}).json()["detail"]
    paystack.mark_success("ref_1", None, None)
    assert "missing api_key_hash" in client.post("/admin/reconcile-transaction", headers=ADMIN, json={"reference": "ref_1"}).json()["detail"]
    paystack.mark_success("ref_1", hashed, "GOLD")
    assert "not recognised" in client.post("/admin/reconcile-transaction", headers=ADMIN, json={"reference": "ref_1"}).json()["detail"]

    paystack.mark_success("ref_1", hashed, "BASIC_MONTHLY")
    r = client.post("/admin/reconcile-transaction", headers=ADMIN, json={"reference": "ref_1"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "reconciled" and body["subscription_code"] == "SUB_basic" and body["email_token"] == "ETK_basic"
    assert body["key"]["subscription_status"] == "active" and body["transaction"]["status"] == "success"
    rec = key_record(key)
    assert rec["active"] and rec["plan_code"] == "BASIC_MONTHLY" and rec["credits"] == 5000

    assert rate_call(client, key).status_code == 200
    r = client.post("/admin/reconcile-transaction", headers=ADMIN, json={"reference": "ref_1"})
    assert r.status_code == 200 and "already reconciled" in r.json()["message"]
    assert key_record(key)["credits"] == 4999, "re-running reconciliation must not re-grant credits"


def test_reconcile_unknown_local_reference_still_upgrades_key(client, paystack):
    key = subscribe(client, "unknownref@example.com").json()["api_key"]
    paystack.mark_success("ref_offline", hash_key(key), "BASIC_MONTHLY")
    r = client.post("/admin/reconcile-transaction", headers=ADMIN, json={"reference": "ref_offline"})
    assert r.status_code == 200 and r.json()["transaction"] is None
    assert key_record(key)["active"] is True and key_record(key)["plan_code"] == "BASIC_MONTHLY"
    assert transactions_for(key)[0]["status"] == "success", "the key's pending transaction is closed out"
    assert activate_via_webhook  # imported for completeness of the billing helpers surface
