"""API key issuance, ownership, rotation, credits, rate limits and access states."""

from __future__ import annotations

import time

import billing_helpers
import helpers
from helpers import (
    ADMIN,
    PASSWORD,
    admin_key,
    free_key,
    key_record,
    patch_key,
    rate_call,
    read_store,
    register_and_login,
    usage,
)


def test_public_free_key_lifecycle(client):
    r = client.post("/auth/free-key", json={"email": " Free.User@Example.com", "first_name": " Free ", "last_name": "User"})
    assert r.status_code == 202 and r.json()["tier"] == "free" and "api_key" not in r.json()
    key = helpers.api_key_from_email("free.user@example.com")
    assert key.startswith("free_") and key not in r.text
    rec = key_record(key)
    assert rec["email"] == "free.user@example.com" and rec["first_name"] == "Free"
    assert rec["tier"] == "free" and rec["credits"] is None and rec["subscription_status"] == "free"
    assert rec["owner"] == "free.user@example.com"
    assert isinstance(rec["created_at"], float) and isinstance(rec["credits_reset_at"], float)

    again = client.post("/auth/free-key", json={"email": "FREE.USER@example.com", "first_name": "X", "last_name": "Y"})
    assert again.status_code == 202 and again.json() == r.json(), "a second request looks exactly like the first"
    assert helpers.last_email_to("free.user@example.com")["Subject"] == "You already have an API key"
    assert len(read_store("keys.json")["keys"]) == 1
    r = client.post("/auth/free-key", json={"email": "n@example.com", "first_name": "", "last_name": "Y"})
    assert r.status_code == 400
    r = client.post("/auth/free-key", json={"email": "not-an-email", "first_name": "N", "last_name": "Y"})
    assert r.status_code == 400 and read_store("keys.json")["keys"][0]["email"] == "free.user@example.com"

    assert rate_call(client, key).status_code == 200
    assert key_record(key)["credits"] == 199
    tracked = read_store("usage.json")["usage"][0]
    assert tracked["email"] == "free.user@example.com" and tracked["total_requests"] == 1 and tracked["billable_requests"] == 1


def test_free_key_usage_snapshot(client):
    key = free_key(client, "snap@example.com")
    for _ in range(5):
        assert rate_call(client, key).status_code == 200
    u = usage(client, key)
    assert u["remaining_credits"] == 194
    assert u["total_requests"] == 6 and u["billable_requests"] == 6 and u["credits_used"] == 6
    assert u["rate_limits"] == {"per_minute": 10, "per_day": 200}
    assert u["key_prefix"] == helpers.hash_key(key)[:8] + "."
    assert u["tier"] == "free" and u["subscription_status"] == "free"
    for field in ("created_at", "credits_reset_at_wat", "first_request_at", "last_request_at"):
        assert isinstance(u[field], str) and "T" in u[field], field


def test_free_key_per_minute_limit(client):
    key = free_key(client, "burst@example.com")
    codes = [rate_call(client, key).status_code for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429
    r = rate_call(client, key)
    assert "per minute" in r.json()["detail"]
    assert key_record(key)["credits"] == 190, "rejected calls must not consume credits"


def test_free_key_per_day_limit(client, monkeypatch):
    monkeypatch.setattr(billing_helpers, "FREE_REQUESTS_PER_MINUTE", 1000)
    monkeypatch.setattr(billing_helpers, "FREE_REQUESTS_PER_DAY", 3)
    key = free_key(client, "daily@example.com")
    assert [rate_call(client, key).status_code for _ in range(3)] == [200] * 3
    r = rate_call(client, key)
    assert r.status_code == 429 and "per day" in r.json()["detail"]


def test_free_credits_exhaust_then_reset_monthly(client):
    key = free_key(client, "poor@example.com")
    patch_key(key, credits=1)
    assert rate_call(client, key).status_code == 200
    r = rate_call(client, key)
    assert r.status_code == 402 and "exhausted" in r.json()["detail"]

    patch_key(key, credits=0, credits_reset_at=time.time() - 5)
    assert rate_call(client, key).status_code == 200
    rec = key_record(key)
    assert rec["credits"] == 199
    assert rec["credits_reset_at"] > time.time() + 29 * 24 * 3600


def test_key_in_query_parameter_is_refused(client):
    key = free_key(client, "qp@example.com")
    r = client.post("/rate", params={"api_key": key}, json={"from_currency": "USD", "to_currency": "NGN"})
    assert r.status_code == 401 and "X-API-KEY header" in r.json()["detail"]
    assert client.get("/docs", params={"api_key": key}).status_code == 401
    assert key_record(key)["credits"] is None, "a refused call is not charged"


def test_missing_invalid_revoked_and_pending_keys(client):
    r = rate_call(client, "")
    assert r.status_code == 401 and r.json()["detail"] == "Missing API Key"
    r = rate_call(client, "free_notreal")
    assert r.status_code == 401 and r.json()["detail"] == "Invalid API Key"

    key = free_key(client, "gone@example.com")
    assert client.post("/admin/revoke-key", headers=ADMIN, json={"api_key": key}).status_code == 200
    r = rate_call(client, key)
    assert r.status_code == 403 and "revoked" in r.json()["detail"]
    assert key_record(key)["subscription_status"] == "revoked" and key_record(key)["revoked_at"]

    headers = register_and_login(client, "pend@example.com")
    paid = client.post("/auth/create-key", headers=headers, json={"tier": "paid"}).json()["api_key"]
    r = rate_call(client, paid)
    assert r.status_code == 402 and "pending" in r.json()["detail"]


def test_session_create_key_free_and_paid(client):
    headers = register_and_login(client, "owner@example.com")
    r = client.post("/auth/create-key", headers=headers, json={"tier": "free"})
    assert r.status_code == 200
    body = r.json()
    assert body["contact"] == {"email": "owner@example.com", "first_name": "Ada", "last_name": "Lovelace"}
    user_id = client.get("/user", headers=headers).json()["user"]["id"]
    assert key_record(body["api_key"])["owner"] == user_id

    r = client.post("/auth/create-key", headers=headers, json={"tier": "PAID", "email": "second@example.com", "first_name": "S", "last_name": "K"})
    assert r.status_code == 400 and "account's email" in r.json()["detail"], "keys use the verified account email"
    assert client.post("/auth/create-key", headers=headers, json={"tier": "gold"}).status_code == 400
    assert client.post("/auth/create-key", headers=headers, json={"tier": "free"}).status_code == 400, "email already tied to a key"
    assert client.post("/auth/create-key", json={"tier": "free"}).status_code == 401

    paid_user = register_and_login(client, "paid-owner@example.com")
    r = client.post("/auth/create-key", headers=paid_user, json={"tier": "PAID", "email": "Paid-Owner@example.com", "first_name": "S"})
    assert r.status_code == 200
    assert r.json()["active"] is False and r.json()["subscription_status"] == "pending"
    assert r.json()["contact"] == {"email": "paid-owner@example.com", "first_name": "S", "last_name": "Lovelace"}
    rec = key_record(r.json()["api_key"])
    assert rec["tier"] == "paid" and rec["plan_code"] is None and rec["active"] is False

    # Further keys join an account by claiming them with the raw key.
    client.post("/user/keys/claim", headers=headers, json={"api_key": free_key(client, "side-project@example.com")})
    keys = client.get("/user/keys", headers=headers).json()["keys"]
    assert len(keys) == 2 and {k["email"] for k in keys} == {"owner@example.com", "side-project@example.com"}
    assert all(k["key_prefix"].endswith(".") and len(k["key_prefix"]) == 9 for k in keys)
    summaries = client.get("/user/usage", headers=paid_user).json()["usage"]
    assert len(summaries) == 1 and summaries[0]["rate_limits"] == {"per_minute": 100, "per_day": 10000}


def test_claim_requires_raw_key_and_never_matches_by_email(client):
    victim_key = free_key(client, "victim@example.com")
    attacker = register_and_login(client, "victim@example.com")  # same email, never owned the key
    assert client.get("/user/keys", headers=attacker).json()["keys"] == []
    r = client.post("/user/keys/regenerate-by-prefix", headers=attacker, json={"key_prefix": "", "password": PASSWORD})
    assert r.status_code == 404
    assert client.post("/user/keys/claim", headers=attacker, json={"api_key": "free_nope"}).status_code == 404

    owner = register_and_login(client, "owner2@example.com")
    r = client.post("/user/keys/claim", headers=owner, json={"api_key": victim_key})
    assert r.status_code == 200 and r.json()["status"] == "claimed"
    assert r.json()["key_prefix"] == helpers.hash_key(victim_key)[:8] + "."
    assert len(client.get("/user/keys", headers=owner).json()["keys"]) == 1
    assert client.post("/user/keys/claim", headers=owner, json={"api_key": victim_key}).status_code == 200, "idempotent"
    r = client.post("/user/keys/claim", headers=attacker, json={"api_key": victim_key})
    assert r.status_code == 409
    assert client.post("/user/keys/regenerate", headers=owner, json={"api_key": victim_key, "password": PASSWORD}).status_code == 200


def test_revoke_and_regenerate_by_raw_key(client):
    headers = register_and_login(client, "rot@example.com")
    mine = client.post("/auth/create-key", headers=headers, json={"tier": "free"}).json()["api_key"]
    theirs = free_key(client, "theirs@example.com")

    assert client.post("/user/keys/revoke", headers=headers, json={"api_key": mine, "password": "wrong"}).status_code == 401
    assert client.post("/user/keys/revoke", headers=headers, json={"api_key": theirs, "password": PASSWORD}).status_code == 404
    assert rate_call(client, theirs).status_code == 200, "someone else's key is untouched"

    r = client.post("/user/keys/regenerate", headers=headers, json={"api_key": mine, "password": PASSWORD})
    assert r.status_code == 200
    new_key = r.json()["api_key"]
    assert new_key != mine and new_key.startswith("free_")
    assert r.json()["contact"]["email"] == "rot@example.com" and r.json()["tier"] == "free"
    assert rate_call(client, mine).status_code == 401
    assert rate_call(client, new_key).status_code == 200

    r = client.post("/user/keys/revoke", headers=headers, json={"api_key": new_key, "password": PASSWORD})
    assert r.status_code == 200 and r.json()["status"] == "revoked"
    assert rate_call(client, new_key).status_code == 403
    assert client.get("/user/keys", headers=headers).json()["keys"][0]["revoked"] is True


def test_revoke_and_regenerate_by_prefix(client):
    headers = register_and_login(client, "pre@example.com")
    key = client.post("/auth/create-key", headers=headers, json={"tier": "free"}).json()["api_key"]
    prefix = client.get("/user/keys", headers=headers).json()["keys"][0]["key_prefix"].rstrip(".")
    assert prefix == helpers.hash_key(key)[:8]

    assert client.post("/user/keys/regenerate-by-prefix", headers=headers, json={"key_prefix": "zzzzzzzz", "password": PASSWORD}).status_code == 404
    r = client.post("/user/keys/regenerate-by-prefix", headers=headers, json={"key_prefix": prefix, "password": PASSWORD})
    assert r.status_code == 200
    new_key = r.json()["api_key"]
    assert r.json()["key_prefix"] == helpers.hash_key(new_key)[:8] + "."
    assert rate_call(client, key).status_code == 401 and rate_call(client, new_key).status_code == 200

    new_prefix = helpers.hash_key(new_key)[:8]
    r = client.post("/user/keys/revoke-by-prefix", headers=headers, json={"key_prefix": new_prefix, "password": PASSWORD})
    assert r.status_code == 200 and r.json()["key_prefix"] == new_prefix + "..."
    assert rate_call(client, new_key).status_code == 403


def test_paid_plan_key_limits_and_historical_access(client):
    key = admin_key(client, "biz", "biz@example.com", plan_code="PRO_MONTHLY")
    rec = key_record(key)
    assert rec["credits"] == 20000 and rec["plan_code"] == "PRO_MONTHLY" and rec["active"] is True
    assert isinstance(rec["upgraded_at"], float)
    assert rate_call(client, key).status_code == 200
    r = client.post("/rate/historical", headers={"X-API-KEY": key}, json={"from_currency": "USD", "to_currency": "NGN", "date": "2024-12-31"})
    assert r.status_code == 200
    u = usage(client, key)
    assert u["remaining_credits"] == 20000 - 3
    assert u["rate_limits"] == {"per_minute": 300, "per_day": 50000}


def test_free_key_cannot_use_historical_endpoints(client):
    key = free_key(client, "hist@example.com")
    for path, body in (
        ("/rate/historical", {"from_currency": "USD", "to_currency": "NGN", "date": "2024-12-31"}),
        ("/convert/historical", {"from_currency": "USD", "to_currency": "NGN", "date": "2024-12-31", "amount": 1}),
        ("/rates/historical", {"base_currency": "USD", "date": "2024-12-31"}),
    ):
        r = client.post(path, headers={"X-API-KEY": key}, json=body)
        assert r.status_code == 403, path


def test_forbidden_historical_call_is_not_charged(client):
    key = free_key(client, "charged@example.com")
    for _ in range(12):  # more than the free per-minute limit: refusals must not count against it either
        r = client.post("/rate/historical", headers={"X-API-KEY": key}, json={"from_currency": "USD", "to_currency": "NGN", "date": "2024-12-31"})
        assert r.status_code == 403
    u = usage(client, key)
    assert u["remaining_credits"] == 199 and u["total_requests"] == 1, "only the /usage call itself was charged"
    assert rate_call(client, key).status_code == 200, "no rate-limit slots were consumed by the refusals"


def test_admin_key_without_plan_runs_on_default_paid_limits(client):
    key = admin_key(client, "noplan", "noplan@example.com")
    assert rate_call(client, key).status_code == 200
    r = client.post("/rate/historical", headers={"X-API-KEY": key}, json={"from_currency": "USD", "to_currency": "NGN", "date": "2024-12-31"})
    assert r.status_code == 200, "a plan-less paid key is still a paid key"
    u = usage(client, key)
    assert u["rate_limits"] == {"per_minute": 100, "per_day": 10000}
    assert u["remaining_credits"] is None and u["billable_requests"] == 0, "no plan means unlimited credits"
    assert u["total_requests"] == 3

    pending = admin_key(client, "later", "later@example.com", activate=False)
    assert rate_call(client, pending).status_code == 402, "an inactive/pending paid key is still refused"
