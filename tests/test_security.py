"""Security regressions: key-hash leaks, billing authorisation and hardening."""

from __future__ import annotations

import hmac
import os
import subprocess
import sys
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import config
import helpers
import rate_limit
from helpers import (
    ADMIN,
    PASSWORD,
    activate_via_webhook,
    free_key,
    hash_key,
    key_record,
    rate_call,
    read_store,
    register,
    register_and_login,
    subscribe,
    subscribe_key,
    transactions_for,
    upgrade,
    verification_for,
    write_store,
)


def _paid_victim(client, paystack, email="victim@example.com") -> str:
    key = subscribe_key(client, email)
    activate_via_webhook(client, paystack, key, "BASIC_MONTHLY", "ref_1")
    return key


def test_duplicate_email_errors_do_not_reveal_the_key_hash(client, paystack):
    victim = _paid_victim(client, paystack)
    victim_hash = hash_key(victim)

    # Any caller holding a public free key can try to move its contact email onto the victim's.
    attacker_key = free_key(client, "attacker@example.com")
    r = upgrade(client, attacker_key, "BASIC_MONTHLY", email="victim@example.com", first_name="M", last_name="X")
    assert r.status_code == 400 and victim_hash[:4] not in r.text

    # Any logged-in user can ask /auth/create-key for the victim's email.
    session = register_and_login(client, "mallory@example.com")
    for tier in ("free", "paid"):
        r = client.post("/auth/create-key", headers=session, json={"tier": tier, "email": "victim@example.com"})
        assert r.status_code == 400 and victim_hash[:4] not in r.text, tier


def test_keys_cannot_be_bound_to_an_email_the_caller_does_not_control(client):
    taken = free_key(client, "taken@example.com")
    attacker_key = free_key(client, "attacker@example.com")
    session = register_and_login(client, "mallory@example.com")

    # Asking about a taken and a free address gives the same answer, so neither route
    # tells the caller which addresses have keys, and neither reserves an address.
    for route in ("upgrade", "create-key"):
        answers = []
        for email in ("taken@example.com", "unclaimed@example.com"):
            if route == "upgrade":
                r = upgrade(client, attacker_key, "BASIC_MONTHLY", email=email, first_name="M", last_name="X")
            else:
                r = client.post("/auth/create-key", headers=session, json={"tier": "free", "email": email})
            answers.append((r.status_code, r.text))
        assert answers[0] == answers[1] and answers[0][0] == 400, route
    assert key_record(attacker_key)["email"] == "attacker@example.com"
    assert key_record(taken)["email"] == "taken@example.com"
    assert {r["email"] for r in read_store("keys.json")["keys"]} == {"taken@example.com", "attacker@example.com"}


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
    client.post("/user/keys/claim", headers=session, json={"api_key": free_key(client, "two-b@example.com")})
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


def test_verification_codes_never_reach_the_caller(client):
    # Registering someone else's address must not hand back the code that proves the inbox.
    r = register(client, "inbox-owner@example.com")
    v = verification_for("inbox-owner@example.com")
    assert r.status_code == 202 and v["otp"] not in r.text and v["token"] not in r.text

    r = client.post("/auth/resend-verification", json={"email": "inbox-owner@example.com"})
    fresh = verification_for("inbox-owner@example.com")
    assert r.status_code == 200 and fresh["otp"] not in r.text and fresh["token"] not in r.text

    login = client.post("/auth/login", json={"email": "inbox-owner@example.com", "password": PASSWORD})
    assert login.status_code == 403, "the account stays unverified"


def test_verification_codes_are_emailed_to_the_address(client, monkeypatch):
    import notifications

    monkeypatch.setattr(notifications, "PUBLIC_BASE_URL", "https://api.example.com")
    register(client, " Mail.Me@Example.com ")
    stored = read_store("users.json")["users"][0]["verification"]
    message = helpers.last_email_to("mail.me@example.com")
    body = message.get_content()
    assert message["Subject"] == "Verify your email address"
    assert f"Verification code: {stored['otp']}" in body and f"Verification token: {stored['token']}" in body
    link = next(line for line in body.splitlines() if line.startswith("https://api.example.com/auth/verify-email?"))
    r = client.get(link.replace("https://api.example.com", ""))
    assert r.status_code == 200 and r.json()["user"]["email_verified"] is True, "the emailed link verifies the address"

    r = client.post("/auth/resend-verification", json={"email": "mail.me@example.com"})
    assert r.status_code == 200 and len(helpers.emails_to("mail.me@example.com")) == 1, "verified: no new code"


def test_verification_code_is_discarded_after_repeated_wrong_guesses(client):
    register(client, "guess@example.com")
    v = verification_for("guess@example.com")
    wrong = "000000" if v["otp"] != "000000" else "111111"
    post = lambda **body: client.post("/auth/verify-email", json={"email": "guess@example.com", **body})  # noqa: E731
    for _ in range(4):
        assert post(otp=wrong).status_code == 400
    assert read_store("users.json")["users"][0]["verification"]["failed_attempts"] == 4
    assert post(token="verify_wrong").status_code == 400
    assert read_store("users.json")["users"][0]["verification"] is None
    r = post(otp=v["otp"])
    assert r.status_code == 400 and "Invalid or expired" in r.json()["detail"], "the guessed-at code is gone"

    assert client.post("/auth/resend-verification", json={"email": "guess@example.com"}).status_code == 200
    fresh = verification_for("guess@example.com")
    assert "failed_attempts" not in read_store("users.json")["users"][0]["verification"]
    assert post(otp=wrong if fresh["otp"] != wrong else "222222").status_code == 400
    assert post(otp=fresh["otp"]).status_code == 200


def test_cors_never_allows_credentialed_cross_origin_requests(client):
    preflight = {
        "Origin": "https://evil.example",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-session-token",
    }
    r = client.options("/billing/cancel", headers=preflight)
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "*", "the origin is not reflected back"
    assert "access-control-allow-credentials" not in r.headers

    r = client.get("/health", headers={"Origin": "https://evil.example", "Cookie": "session=x"})
    assert r.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in r.headers


def test_cors_origins_are_configured_from_the_environment():
    import config
    from fastapi.middleware.cors import CORSMiddleware

    assert config.parse_cors_origins(None) == ["*"]
    assert config.parse_cors_origins("  ") == ["*"]
    assert config.parse_cors_origins(" https://a.example , https://b.example ,") == ["https://a.example", "https://b.example"]
    cors = next(m for m in helpers.main.app.user_middleware if m.cls is CORSMiddleware)
    assert cors.kwargs["allow_origins"] == config.CORS_ALLOW_ORIGINS
    assert cors.kwargs["allow_credentials"] is False


def test_public_routes_are_rate_limited_per_client(client, monkeypatch):
    for route in list(config.PUBLIC_ROUTE_LIMITS):
        monkeypatch.setitem(config.PUBLIC_ROUTE_LIMITS, route, (2, 100))
    nobody = "nobody@example.com"
    attempts = {
        "free-key": lambda i: client.post("/auth/free-key", json={"email": f"f{i}@example.com", "first_name": "F", "last_name": "K"}),
        "register": lambda i: register(client, f"r{i}@example.com"),
        "login": lambda i: client.post("/auth/login", json={"email": nobody, "password": "guess"}),
        "resend-verification": lambda i: client.post("/auth/resend-verification", json={"email": nobody}),
        "verify-email": lambda i: client.post("/auth/verify-email", json={"email": nobody, "otp": "123456"}),
        "subscribe": lambda i: subscribe(client, f"s{i}@example.com"),
    }
    for name, call in attempts.items():
        codes = [call(i).status_code for i in range(3)]
        assert 429 not in codes[:2] and codes[2] == 429, (name, codes)
    assert len(read_store("keys.json")["keys"]) == 4, "the refused free-key and subscribe calls created nothing"

    # The emailed link shares the code-guessing budget with the OTP form.
    r = client.get("/auth/verify-email", params={"email": nobody, "token": "verify_x"})
    assert r.status_code == 429 and "per minute" in r.json()["detail"]

    # Another client address keeps its own budget.
    other = TestClient(helpers.main.app, client=("203.0.113.9", 4000))
    assert other.post("/auth/login", json={"email": nobody, "password": "guess"}).status_code == 401


def test_client_buckets_group_ipv6_hosts_and_stale_buckets_are_swept(monkeypatch):
    bucket = rate_limit.client_bucket
    assert bucket("2001:db8:1:2::1") == bucket("2001:db8:1:2:ffff::9") == "2001:db8:1:2::/64"
    assert bucket("2001:db8:1:3::1") != bucket("2001:db8:1:2::1")
    assert bucket("::ffff:198.51.100.7") == bucket("198.51.100.7") == "198.51.100.7"
    assert bucket("testclient") == "testclient" and bucket(None) == "unknown"

    now = time.time()
    db = rate_limit._connection()
    db.executemany("INSERT INTO hits (bucket, ts) VALUES (?, ?)", [("public:login:old", now - 90000), ("public:login:fresh", now - 10)])
    monkeypatch.setattr(rate_limit, "SWEEP_EVERY", 1)
    rate_limit.allow_request("k", per_minute=5, per_day=5)
    assert {b for (b,) in db.execute("SELECT DISTINCT bucket FROM hits")} == {"public:login:fresh", "k"}


def test_rate_limits_are_shared_with_other_processes_and_survive_restarts():
    def other_process_hit() -> int:
        code = "import rate_limit; rate_limit.allow_request('shared', per_minute=3, per_day=100)"
        env = {**os.environ, "PYTHONPATH": helpers.PROJECT_ROOT}
        return subprocess.run([sys.executable, "-c", code], cwd=os.getcwd(), env=env, capture_output=True).returncode

    rate_limit.allow_request("shared", per_minute=3, per_day=100)
    rate_limit.allow_request("shared", per_minute=3, per_day=100)
    assert other_process_hit() == 0, "a fresh process sees two hits and takes the third slot"
    assert other_process_hit() != 0, "and another fresh process is refused"
    with pytest.raises(HTTPException) as refused:
        rate_limit.allow_request("shared", per_minute=3, per_day=100)
    assert refused.value.status_code == 429, "this process counts the other process's hit"


def test_account_endpoints_answer_the_same_for_every_email(client, monkeypatch):
    from services import user_service

    helpers.relax_public_limits(monkeypatch)
    register_and_login(client, "known@example.com")
    register(client, "pending@example.com")
    emails = ("fresh@example.com", "known@example.com", "pending@example.com")

    responses = [register(client, email, password="Another123") for email in emails]
    assert {r.status_code for r in responses} == {202} and len({r.text for r in responses}) == 1
    assert len(read_store("users.json")["users"]) == 3, "only the fresh address got an account"
    assert helpers.last_email_to("known@example.com")["Subject"] == "You already have an account"
    assert helpers.last_email_to("pending@example.com")["Subject"] == "You already have an account"
    r = client.post("/auth/login", json={"email": "known@example.com", "password": PASSWORD})
    assert r.status_code == 200, "a second registration does not touch the existing account"

    before = {e: len(helpers.emails_to(e)) for e in ("ghost@example.com", *emails)}
    responses = [client.post("/auth/resend-verification", json={"email": e}) for e in ("ghost@example.com", *emails)]
    assert {r.status_code for r in responses} == {200} and len({r.text for r in responses}) == 1
    sent = {e: len(helpers.emails_to(e)) - n for e, n in before.items()}
    assert sent == {"ghost@example.com": 0, "fresh@example.com": 1, "known@example.com": 0, "pending@example.com": 1}

    responses = [client.post("/auth/verify-email", json={"email": e, "otp": "000000"}) for e in ("ghost@example.com", *emails)]
    assert {r.status_code for r in responses} == {400} and len({r.text for r in responses}) == 1

    hashed = []
    real = user_service._hash_password
    monkeypatch.setattr(user_service, "_hash_password", lambda *a: hashed.append(a) or real(*a))
    unknown = client.post("/auth/login", json={"email": "ghost@example.com", "password": "guess-1234"})
    wrong = client.post("/auth/login", json={"email": "known@example.com", "password": "guess-1234"})
    assert unknown.status_code == wrong.status_code == 401 and unknown.text == wrong.text
    assert len(hashed) == 2, "an unknown email costs the same password hash as a wrong password"


def test_repeated_wrong_passwords_lock_the_account_for_a_while(client, monkeypatch):
    helpers.relax_public_limits(monkeypatch)
    register_and_login(client, "locked@example.com")
    register_and_login(client, "bystander@example.com")
    login = lambda email, pw: client.post("/auth/login", json={"email": email, "password": pw})  # noqa: E731

    assert login("locked@example.com", "wrong-1").status_code == 401
    assert login("locked@example.com", PASSWORD).status_code == 200, "a correct password resets the count"
    for i in range(config.LOGIN_MAX_FAILURES):
        assert login("locked@example.com", f"wrong-{i}").status_code == 401
    r = login("locked@example.com", PASSWORD)
    assert r.status_code == 429 and "Too many failed sign-in attempts" in r.json()["detail"]
    assert login("bystander@example.com", PASSWORD).status_code == 200, "other accounts are unaffected"

    # Password confirmations share the count: a stolen session cannot keep guessing either.
    session = register_and_login(client, "confirm@example.com")
    for i in range(config.LOGIN_MAX_FAILURES):
        r = client.post("/user/keys/revoke", headers=session, json={"api_key": "free_x", "password": f"wrong-{i}"})
        assert r.status_code == 401
    r = client.post("/user/keys/revoke", headers=session, json={"api_key": "free_x", "password": PASSWORD})
    assert r.status_code == 429

    # Unknown emails lock the same way, so the lockout says nothing about who has an account.
    for i in range(config.LOGIN_MAX_FAILURES):
        assert login("nobody@example.com", f"wrong-{i}").status_code == 401
    assert login("nobody@example.com", "anything").status_code == 429

    later = time.time() + config.LOGIN_LOCKOUT_SECONDS + 1
    monkeypatch.setattr(rate_limit.time, "time", lambda: later)
    assert login("locked@example.com", PASSWORD).status_code == 200, "the lock lifts once failures age out"


def test_free_keys_go_only_to_the_inbox(client):
    victim_key = free_key(client, "owner-of-inbox@example.com")

    # Asking for a key in someone else's name hands the caller nothing.
    fresh = client.post("/auth/free-key", json={"email": "someone-else@example.com", "first_name": "M", "last_name": "X"})
    taken = client.post("/auth/free-key", json={"email": "owner-of-inbox@example.com", "first_name": "M", "last_name": "X"})
    assert fresh.status_code == taken.status_code == 202 and fresh.text == taken.text
    issued = helpers.api_key_from_email("someone-else@example.com")
    assert issued not in fresh.text, "the new key reached only its inbox"
    assert helpers.last_email_to("owner-of-inbox@example.com")["Subject"] == "You already have an API key"
    assert victim_key not in helpers.last_email_to("owner-of-inbox@example.com").get_content()
    assert len(read_store("keys.json")["keys"]) == 2


def test_subscribe_cannot_place_a_key_in_someone_elses_account(client):
    session = register_and_login(client, "account-owner@example.com")
    user_id = client.get("/user", headers=session).json()["user"]["id"]
    r = subscribe(client, "planted@example.com", owner=user_id)
    assert r.status_code == 202
    assert read_store("keys.json")["keys"][0]["owner"] == "planted@example.com"
    assert client.get("/user/keys", headers=session).json()["keys"] == []


def test_paid_keys_from_subscribe_go_only_to_the_inbox(client, paystack):
    owner_key = subscribe_key(client, "payer@example.com")
    fresh = subscribe(client, "someone-else-paid@example.com")
    taken = subscribe(client, "payer@example.com")
    assert fresh.status_code == taken.status_code == 202 and fresh.text == taken.text
    issued = helpers.api_key_from_email("someone-else-paid@example.com")
    checkout = helpers.checkout_from_email("someone-else-paid@example.com")
    assert issued not in fresh.text and checkout["authorization_url"] not in fresh.text
    assert owner_key not in helpers.last_email_to("payer@example.com").get_content()
    assert paystack.init_count == 2, "no checkout is started for a taken address"


def test_billing_calls_are_rate_limited_per_key(client, monkeypatch):
    import billing_helpers

    monkeypatch.setattr(billing_helpers, "BILLING_REQUESTS_PER_MINUTE", 3)
    session = register_and_login(client, "biller@example.com")
    key = client.post("/auth/create-key", headers=session, json={"tier": "free"}).json()["api_key"]
    by_session = {**session, "X-API-KEY-PREFIX": hash_key(key)[:8]}
    codes = [
        client.get("/transactions", headers={"X-API-KEY": key}).status_code,
        client.post("/billing/upgrade", headers={"X-API-KEY": key}, json={"plan_code": "BASIC_MONTHLY"}).status_code,
        client.get("/transactions", headers=by_session).status_code,
    ]
    assert codes == [200, 200, 200]
    r = client.post("/billing/cancel", headers=by_session)
    assert r.status_code == 429 and "per minute" in r.json()["detail"], "both ways of naming the key share one budget"

    assert client.get("/transactions", headers={"X-API-KEY": free_key(client, "other-biller@example.com")}).status_code == 200
    assert rate_call(client, key).status_code == 200, "billing calls do not use the key's API rate limit"


def test_unsigned_webhooks_cannot_fill_the_disk(client, monkeypatch):
    import json

    import routers.billing

    monkeypatch.setattr(routers.billing, "WEBHOOK_MAX_BODY_BYTES", 2048)
    monkeypatch.setattr(routers.billing, "WEBHOOK_REJECTED_LOG_LIMITS", (3, 100))
    post = lambda body, sig="bad", **kw: client.post(  # noqa: E731
        "/paystack/webhook", content=body, headers={"x-paystack-signature": sig, "content-type": "application/json"}, **kw
    )
    padded = b'{"event": "charge.success", "pad": "' + b"A" * 1500 + b'"}'
    assert post(padded).status_code == 400
    assert post(b"x" * 4096, sig=helpers.sign(b"x" * 4096)).status_code == 413, "too large even when signed"
    assert post(iter([b"y" * 1500, b"y" * 1500])).status_code == 413, "chunked bodies are measured as they stream"
    for _ in range(5):
        assert post(b"{}").status_code == 400

    with open("webhook_logs.json", encoding="utf-8") as fh:
        entries = [json.loads(line) for line in fh]
    assert [e["rejected"] for e in entries] == ["invalid signature", "body too large", "body too large"], "throttled"
    assert entries[0]["size"] == len(padded) and all("body" not in e for e in entries)

    signed = json.dumps({"event": "transfer.success", "data": {}}).encode()
    assert post(signed, sig=helpers.sign(signed)).status_code == 200, "verified deliveries still go through"
