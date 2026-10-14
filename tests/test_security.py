"""Security regressions: key-hash leaks, billing authorisation and hardening."""

from __future__ import annotations

import hmac

from helpers import (
    ADMIN,
    PASSWORD,
    activate_via_webhook,
    free_key,
    hash_key,
    key_record,
    read_store,
    register_and_login,
    subscribe,
    transactions_for,
    upgrade,
    write_store,
)


def _paid_victim(client, paystack, email="victim@example.com") -> str:
    key = subscribe(client, email).json()["api_key"]
    activate_via_webhook(client, paystack, key, "BASIC_MONTHLY", "ref_1")
    return key


def test_duplicate_email_errors_do_not_reveal_the_key_hash(client, paystack):
    victim = _paid_victim(client, paystack)
    victim_hash = hash_key(victim)

    # Any caller holding a public free key can try to move its contact email onto the victim's.
    attacker_key = free_key(client, "attacker@example.com")
    r = upgrade(client, attacker_key, "BASIC_MONTHLY", email="victim@example.com", first_name="M", last_name="X")
    assert r.status_code == 400 and "already associated" in r.json()["detail"]
    assert victim_hash[:4] not in r.text

    # Any logged-in user can ask /auth/create-key for the victim's email.
    session = register_and_login(client, "mallory@example.com")
    for tier in ("free", "paid"):
        r = client.post("/auth/create-key", headers=session, json={"tier": tier, "email": "victim@example.com"})
        assert r.status_code == 400 and "already associated" in r.json()["detail"]
        assert victim_hash[:4] not in r.text, tier


def test_hash_prefix_alone_cannot_drive_billing(client, paystack):
    victim = _paid_victim(client, paystack)
    victim_hash = hash_key(victim)
    attempts = [
        {"headers": {"X-API-KEY-PREFIX": victim_hash[:8]}},
        {"headers": {"X-API-KEY-PREFIX": victim_hash}},
        {"params": {"api_key_prefix": victim_hash[:8]}},
    ]
    for auth in attempts:
        r = client.post("/billing/cancel", **auth)
        assert r.status_code == 401, (auth, r.text)
        r = client.post("/billing/upgrade", json={"plan_code": "PRO_MONTHLY", "email": "mallory@example.com"}, **auth)
        assert r.status_code == 401, (auth, r.text)
        r = client.get("/transactions", **auth)
        assert r.status_code == 401, (auth, r.text)
        r = client.post("/billing/verify", json={"reference": "ref_1"}, **auth)
        assert r.status_code == 401, (auth, r.text)

    # A session does not help unless it owns the key.
    session = register_and_login(client, "mallory@example.com")
    r = client.post("/billing/cancel", headers={**session, "X-API-KEY-PREFIX": victim_hash[:8]})
    assert r.status_code == 404

    rec = key_record(victim)
    assert rec["tier"] == "paid" and rec["plan_code"] == "BASIC_MONTHLY" and rec["subscription_status"] == "active"
    assert rec["email"] == "victim@example.com" and rec["pending_plan_code"] is None
    assert paystack.disabled == [], "the victim's Paystack subscription must not be cancelled"
    assert [t["status"] for t in transactions_for(victim)] == ["success"]


def test_session_owner_can_run_billing_for_its_key_by_prefix(client, paystack):
    session = register_and_login(client, "owner@example.com")
    key = client.post("/auth/create-key", headers=session, json={"tier": "free"}).json()["api_key"]
    auth = {**session, "X-API-KEY-PREFIX": hash_key(key)[:8]}

    r = client.post("/billing/upgrade", headers=auth, json={"plan_code": "BASIC_MONTHLY"})
    assert r.status_code == 200 and key_record(key)["pending_plan_code"] == "BASIC_MONTHLY"
    r = client.get("/transactions", headers=auth)
    assert r.status_code == 200 and [t["reference"] for t in r.json()["transactions"]] == ["ref_1"]

    activate_via_webhook(client, paystack, key, "BASIC_MONTHLY", "ref_1")
    r = client.post("/billing/cancel", headers=auth)
    assert r.status_code == 200 and key_record(key)["tier"] == "free"

    r = client.post("/billing/cancel", headers=session)
    assert r.status_code == 400 and "X-API-KEY-PREFIX" in r.json()["detail"]
    other = free_key(client, "other@example.com")
    assert client.get("/transactions", headers={**session, "X-API-KEY-PREFIX": hash_key(other)[:8]}).status_code == 404
    assert client.get("/transactions", headers={"X-SESSION-TOKEN": "session_bogus", "X-API-KEY-PREFIX": hash_key(key)[:8]}).status_code == 401


def test_session_prefix_must_name_exactly_one_owned_key(client):
    session = register_and_login(client, "two@example.com")
    client.post("/auth/create-key", headers=session, json={"tier": "free"})
    client.post("/auth/create-key", headers=session, json={"tier": "free", "email": "two-b@example.com"})
    data = read_store("keys.json")
    data["keys"][0]["key_hash"] = "aaaa1111" + "0" * 56
    data["keys"][1]["key_hash"] = "aaaa2222" + "0" * 56
    write_store("keys.json", data)

    r = client.get("/transactions", headers={**session, "X-API-KEY-PREFIX": "aaaa"})
    assert r.status_code == 400 and "more than one" in r.json()["detail"]
    assert client.get("/transactions", headers={**session, "X-API-KEY-PREFIX": "aaaa2"}).status_code == 200


def _spy_compare_digest(monkeypatch):
    calls = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    return calls


def test_admin_token_is_compared_in_constant_time(client, monkeypatch):
    calls = _spy_compare_digest(monkeypatch)
    assert client.get("/admin/keys", headers={"X-ADMIN-TOKEN": "wrong"}).status_code == 403
    assert client.get("/admin/keys", headers=ADMIN).status_code == 200
    assert (b"wrong", ADMIN["X-ADMIN-TOKEN"].encode()) in calls
    assert (ADMIN["X-ADMIN-TOKEN"].encode(), ADMIN["X-ADMIN-TOKEN"].encode()) in calls
    r = client.get("/admin/keys", headers={"X-ADMIN-TOKEN": "t\xe9st".encode("latin-1")})
    assert r.status_code == 403, "a non-ASCII token is a mismatch, not a server error"
    assert client.get("/admin/keys").status_code == 403


def test_password_hash_is_compared_in_constant_time(client, monkeypatch):
    register_and_login(client, "pw@example.com")
    stored = read_store("users.json")["users"][0]["password_hash"].encode()
    calls = _spy_compare_digest(monkeypatch)
    login = lambda pw: client.post("/auth/login", json={"email": "pw@example.com", "password": pw})  # noqa: E731
    assert login("wrong-password").status_code == 401
    assert login(PASSWORD).status_code == 200
    assert len(calls) == 2 and all(stored_arg == stored for _, stored_arg in calls)

    data = read_store("users.json")
    data["users"][0]["password_hash"] = None
    write_store("users.json", data)
    assert login(PASSWORD).status_code == 401, "a record without a hash refuses the login"


def test_credentials_are_refused_in_the_query_string(client):
    key = free_key(client, "query@example.com")
    session = register_and_login(client, "query-user@example.com")
    token = session["X-SESSION-TOKEN"]

    r = client.get("/transactions", params={"api_key": key})
    assert r.status_code == 401 and "X-API-KEY header" in r.json()["detail"]
    r = client.post("/billing/cancel", params={"session_token": token, "api_key_prefix": hash_key(key)[:8]})
    assert r.status_code == 401 and "X-SESSION-TOKEN header" in r.json()["detail"]

    # Logout must not quietly succeed while leaving the session alive.
    r = client.post("/auth/logout", params={"session_token": token})
    assert r.status_code == 401 and "X-SESSION-TOKEN header" in r.json()["detail"]
    assert client.get("/user", headers=session).status_code == 200
    assert client.post("/auth/logout", headers=session).status_code == 200
    assert client.get("/user", headers=session).status_code == 401

    # A header wins; an unrelated query string is not a credential.
    assert client.get("/usage", headers={"X-API-KEY": key}, params={"page": 1}).status_code == 200
