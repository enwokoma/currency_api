"""Password reset: forgot-password, reset-password and their safeguards."""

from __future__ import annotations

import hashlib
import json
import time

import config
import helpers
from helpers import PASSWORD, read_store, register, register_and_login, write_store

NEW = "BrandNewPass456"


def _forgot(client, email):
    return client.post("/auth/forgot-password", json={"email": email})


def _reset(client, email, token, new=NEW, confirm=None):
    body = {"email": email, "token": token, "new_password": new, "confirm_password": confirm or new}
    return client.post("/auth/reset-password", json=body)


def _login(client, email, password):
    return client.post("/auth/login", json={"email": email, "password": password})


def _user(email):
    return next(u for u in read_store("users.json")["users"] if u["email"] == email)


def test_reset_sets_the_new_password_and_signs_out_every_session(client):
    first = register_and_login(client, "reset@example.com")
    second = {"X-SESSION-TOKEN": _login(client, "reset@example.com", PASSWORD).json()["session_token"]}

    r = _forgot(client, " Reset@Example.com ")
    assert r.status_code == 202
    assert helpers.last_email_to("reset@example.com")["Subject"] == "Reset your password"
    token = helpers.reset_token_from_email("reset@example.com")
    assert token.startswith("reset_") and token not in r.text

    r = _reset(client, "Reset@Example.com", token)
    assert r.status_code == 200 and "signed out" in r.json()["message"]
    assert helpers.last_email_to("reset@example.com")["Subject"] == "Your password was changed"
    for session in (first, second):
        assert client.get("/user", headers=session).status_code == 401, "every old session is revoked"
    assert _login(client, "reset@example.com", PASSWORD).status_code == 401
    assert _login(client, "reset@example.com", NEW).status_code == 200
    assert _reset(client, "reset@example.com", token, new="Another789xyz").status_code == 400, "a token works once"


def test_forgot_and_reset_answer_the_same_for_every_email(client, monkeypatch):
    helpers.relax_public_limits(monkeypatch)
    register_and_login(client, "known-reset@example.com")
    register_and_login(client, "never-asked@example.com")

    known, unknown = _forgot(client, "known-reset@example.com"), _forgot(client, "ghost-reset@example.com")
    assert known.status_code == unknown.status_code == 202 and known.text == unknown.text
    assert helpers.emails_to("ghost-reset@example.com") == [], "no mail to addresses without an account"
    token = helpers.reset_token_from_email("known-reset@example.com")

    failures = [
        _reset(client, "ghost-reset@example.com", token),
        _reset(client, "never-asked@example.com", token),
        _reset(client, "known-reset@example.com", "reset_wrong"),
    ]
    data = read_store("users.json")
    next(u for u in data["users"] if u["email"] == "known-reset@example.com")["password_reset"]["expires_at"] = time.time() - 1
    write_store("users.json", data)
    failures.append(_reset(client, "known-reset@example.com", token))
    assert {r.status_code for r in failures} == {400} and len({r.text for r in failures}) == 1
    assert "Invalid or expired reset token" in failures[0].json()["detail"]
    assert _login(client, "known-reset@example.com", PASSWORD).status_code == 200, "nothing changed"


def test_only_the_latest_token_works_and_only_its_hash_is_stored(client):
    register_and_login(client, "rotate@example.com")
    _forgot(client, "rotate@example.com")
    first = helpers.reset_token_from_email("rotate@example.com")
    _forgot(client, "rotate@example.com")
    second = helpers.reset_token_from_email("rotate@example.com")
    assert first != second

    stored = json.dumps(read_store("users.json"))
    assert first not in stored and second not in stored, "the users file alone cannot reset a password"
    assert _user("rotate@example.com")["password_reset"]["token_hash"] == hashlib.sha256(second.encode()).hexdigest()
    assert _reset(client, "rotate@example.com", first).status_code == 400, "a newer request replaces the old token"
    assert _reset(client, "rotate@example.com", second).status_code == 200
    assert _user("rotate@example.com")["password_reset"] is None


def test_password_rules_are_checked_without_spending_the_token(client):
    register_and_login(client, "rules@example.com")
    _forgot(client, "rules@example.com")
    token = helpers.reset_token_from_email("rules@example.com")
    r = _reset(client, "rules@example.com", token, new="short1")
    assert r.status_code == 400 and "at least 8" in r.json()["detail"]
    r = _reset(client, "rules@example.com", token, confirm="Mismatch1234")
    assert r.status_code == 400 and "do not match" in r.json()["detail"]
    assert _reset(client, "rules@example.com", token).status_code == 200


def test_reset_lifts_the_sign_in_lockout_and_proves_the_address(client, monkeypatch):
    helpers.relax_public_limits(monkeypatch)
    register_and_login(client, "lockedout@example.com")
    for i in range(config.LOGIN_MAX_FAILURES):
        _login(client, "lockedout@example.com", f"wrong-{i}")
    assert _login(client, "lockedout@example.com", PASSWORD).status_code == 429
    _forgot(client, "lockedout@example.com")
    assert _reset(client, "lockedout@example.com", helpers.reset_token_from_email("lockedout@example.com")).status_code == 200
    assert _login(client, "lockedout@example.com", NEW).status_code == 200, "the owner gets back in at once"

    # Holding the emailed token proves the inbox, like a verification code does.
    register(client, "never-verified@example.com")
    assert _login(client, "never-verified@example.com", PASSWORD).status_code == 403
    _forgot(client, "never-verified@example.com")
    token = helpers.reset_token_from_email("never-verified@example.com")
    assert _reset(client, "never-verified@example.com", token).status_code == 200
    assert _login(client, "never-verified@example.com", NEW).status_code == 200
