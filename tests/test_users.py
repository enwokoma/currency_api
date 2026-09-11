"""Registration, email verification, login, sessions, logout."""

from __future__ import annotations

import time

import helpers
from helpers import PASSWORD, read_store, register, register_and_login, write_store


def test_register_normalises_email_and_returns_verification(client):
    r = register(client, " Ada@Example.COM ")
    assert r.status_code == 201
    body = r.json()
    assert body["user"]["email"] == "ada@example.com"
    assert body["user"]["email_verified"] is False
    assert not {"password_hash", "salt", "verification"} & set(body["user"])
    v = body["verification"]
    assert len(v["otp"]) == 6 and v["otp"].isdigit()
    assert v["token"].startswith("verify_")
    assert v["expires_at"] > time.time()
    stored = read_store("users.json")["users"][0]
    assert stored["password_hash"] != PASSWORD and len(stored["salt"]) == 32


def test_register_validation(client):
    assert register(client, "a@example.com", confirm="different").status_code == 400
    assert register(client, "a@example.com", password="short1", confirm="short1").status_code == 400
    assert register(client, "a@example.com").status_code == 201
    r = register(client, "A@EXAMPLE.com")
    assert r.status_code == 400 and "already registered" in r.json()["detail"]


def test_verify_email_requires_exactly_one_credential(client):
    v = register(client, "v@example.com").json()["verification"]
    post = lambda **body: client.post("/auth/verify-email", json={"email": "v@example.com", **body})  # noqa: E731
    assert post(otp=v["otp"], token=v["token"]).status_code == 400
    assert post().status_code == 400
    assert post(otp="000000").status_code == 400 and "Invalid OTP" in post(otp="000000").json()["detail"]
    assert "Invalid verification link" in post(token="verify_wrong").json()["detail"]
    assert client.post("/auth/verify-email", json={"email": "nobody@example.com", "otp": v["otp"]}).status_code == 404

    r = post(otp=v["otp"])
    assert r.status_code == 200 and r.json()["user"]["email_verified"] is True
    assert post(otp=v["otp"]).status_code == 200, "verifying again is idempotent"
    assert client.post("/auth/login", json={"email": "v@example.com", "password": PASSWORD}).status_code == 200


def test_verify_email_via_link(client):
    v = register(client, "link@example.com").json()["verification"]
    assert client.get("/auth/verify-email", params={"email": "link@example.com", "token": "verify_x"}).status_code == 400
    assert client.get("/auth/verify-email", params={"email": "link@example.com"}).status_code == 422
    r = client.get("/auth/verify-email", params={"email": "LINK@example.com", "token": v["token"]})
    assert r.status_code == 200 and r.json()["user"]["email_verified"] is True


def test_expired_verification_code_is_rejected_and_resend_recovers(client):
    v = register(client, "exp@example.com").json()["verification"]
    data = read_store("users.json")
    data["users"][0]["verification"]["expires_at"] = time.time() - 1
    write_store("users.json", data)
    r = client.post("/auth/verify-email", json={"email": "exp@example.com", "otp": v["otp"]})
    assert r.status_code == 400 and "expired" in r.json()["detail"]

    r = client.post("/auth/resend-verification", json={"email": "exp@example.com"})
    assert r.status_code == 200
    fresh = r.json()["verification"]
    assert fresh["token"] != v["token"] and fresh["expires_at"] > time.time()
    assert client.post("/auth/verify-email", json={"email": "exp@example.com", "otp": fresh["otp"]}).status_code == 200


def test_resend_verification_edge_cases(client):
    assert client.post("/auth/resend-verification", json={"email": "ghost@example.com"}).status_code == 404
    register_and_login(client, "done@example.com")
    r = client.post("/auth/resend-verification", json={"email": "done@example.com"})
    assert r.status_code == 400 and "already verified" in r.json()["detail"]


def test_login_paths(client):
    register(client, "log@example.com")
    login = lambda pw="StrongPass123", email="log@example.com": client.post("/auth/login", json={"email": email, "password": pw})  # noqa: E731
    assert login().status_code == 403, "unverified users cannot log in"
    otp = client.post("/auth/resend-verification", json={"email": "log@example.com"}).json()["verification"]["otp"]
    client.post("/auth/verify-email", json={"email": "log@example.com", "otp": otp})
    assert login(pw="wrong-password").status_code == 401
    assert login(email="nobody@example.com").status_code == 401
    r = login()
    assert r.status_code == 200
    body = r.json()
    assert body["session_token"].startswith("session_")
    assert abs(body["expires_at"] - (time.time() + 24 * 3600)) < 60
    me = client.get("/user", headers={"X-SESSION-TOKEN": body["session_token"]})
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "log@example.com" and me.json()["user"]["last_login_at"]


def test_session_validation(client):
    headers = register_and_login(client, "sess@example.com")
    assert client.get("/user").status_code == 401
    assert client.get("/user", headers={"X-SESSION-TOKEN": "session_bogus"}).status_code == 401
    assert client.get("/user", params={"session_token": headers["X-SESSION-TOKEN"]}).status_code == 200

    data = read_store("sessions.json")
    data["sessions"][0]["expires_at"] = time.time() - 1
    write_store("sessions.json", data)
    r = client.get("/user", headers=headers)
    assert r.status_code == 401 and "expired" in r.json()["detail"]
    assert read_store("sessions.json")["sessions"][0]["revoked"] is True


def test_logout_revokes_session(client):
    headers = register_and_login(client, "out@example.com")
    assert client.post("/auth/logout", headers=headers).status_code == 200
    assert client.get("/user", headers=headers).status_code == 401
    assert client.post("/auth/logout").status_code == 200, "logout without a token is a harmless no-op"


def test_sanitized_user_shape(client):
    headers = register_and_login(client, "shape@example.com", first=" Grace ", last="Hopper")
    user = client.get("/user", headers=headers).json()["user"]
    assert set(user) == {"id", "first_name", "last_name", "email", "email_verified", "created_at", "updated_at", "last_login_at"}
    assert user["first_name"] == "Grace"
    assert helpers  # keep import used
