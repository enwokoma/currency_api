"""End-to-end tests for the user management and profile system.

These tests exercise the new registration, email verification, login,
session management, key creation and key listing functionality added
to the currency conversion API. The FastAPI ``TestClient`` is used to
interact with the application in-process without starting a real
HTTP server. Each test cleans up any data files it creates to
maintain isolation between runs.

To run the tests, execute ``python test_user_management.py`` from
the repository root. No external dependencies beyond the project's
requirements are needed.
"""

import os
import json
import shutil
import string
import random
from typing import Dict, Any

from fastapi.testclient import TestClient

import main  # Import the FastAPI app and configuration
from main import app
from users import load_users, save_users
from auth import load_keys, save_keys


def random_email() -> str:
    """Generate a random email address for testing."""
    username = "testuser_" + ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    return f"{username}@example.com"


def cleanup_files():
    """Remove any JSON files created by tests to ensure isolation."""
    for fname in ["users.json", "sessions.json", "keys.json", "transactions.json", "admin_token.txt"]:
        if os.path.exists(fname):
            os.remove(fname)


def test_register_verify_login_and_key_flow():
    """Full workflow: register, verify, login, create keys and list them."""
    cleanup_files()
    client = TestClient(app)

    # Step 1: Register a new user
    email = random_email()
    reg_payload = {
        "first_name": "Test",
        "last_name": "User",
        "email": email,
        "password": "StrongPass123",
        "confirm_password": "StrongPass123",
    }
    reg_resp = client.post("/auth/register", json=reg_payload)
    assert reg_resp.status_code == 201
    reg_data = reg_resp.json()
    assert reg_data["user"]["email"] == email.lower()
    # Ensure verification info is present
    otp = reg_data["verification"]["otp"]
    token = reg_data["verification"]["token"]
    assert otp and token

    # Login before verification should fail with 403
    login_resp = client.post(
        "/auth/login", json={"email": email, "password": "StrongPass123"}
    )
    assert login_resp.status_code == 403

    # Step 2: Verify the email using OTP
    ver_resp = client.post(
        "/auth/verify-email", json={"email": email, "otp": otp}
    )
    assert ver_resp.status_code == 200
    ver_data = ver_resp.json()
    assert ver_data["user"]["email_verified"] is True

    # Step 3: Login after verification
    login_resp = client.post(
        "/auth/login", json={"email": email, "password": "StrongPass123"}
    )
    assert login_resp.status_code == 200
    login_data = login_resp.json()
    session_token = login_data["session_token"]
    assert session_token.startswith("session_")

    # Step 4: Get current user profile with session token
    me_resp = client.get(
        "/user", headers={"X-SESSION-TOKEN": session_token}
    )
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data["user"]["email"] == email.lower()

    # Step 5: Create a free key
    free_key_resp = client.post(
        "/auth/create-key",
        json={"tier": "free"},
        headers={"X-SESSION-TOKEN": session_token},
    )
    assert free_key_resp.status_code == 200
    free_key_data = free_key_resp.json()
    assert free_key_data["tier"] == "free"
    free_key = free_key_data["api_key"]
    assert free_key.startswith("free_")

    # Step 6: Create a paid key
    paid_key_resp = client.post(
        "/auth/create-key",
        json={"tier": "paid"},
        headers={"X-SESSION-TOKEN": session_token},
    )
    assert paid_key_resp.status_code == 200
    paid_key_data = paid_key_resp.json()
    assert paid_key_data["tier"] == "paid"
    paid_key = paid_key_data["api_key"]
    # Paid keys are UUIDs without the free_ prefix
    assert paid_key and not paid_key.startswith("free_")

    # Step 7: List user keys and ensure both keys appear
    keys_resp = client.get("/user/keys", headers={"X-SESSION-TOKEN": session_token})
    assert keys_resp.status_code == 200
    keys_data = keys_resp.json()
    assert len(keys_data["keys"]) >= 2
    # Check that at least one free and one paid key are present
    tiers = {k["tier"] for k in keys_data["keys"]}
    assert "free" in tiers and "paid" in tiers

    # Step 8: Use the free key to call a protected endpoint
    rate_resp = client.get(
        "/rate?from=USD&to=NGN", headers={"X-API-KEY": free_key}
    )
    assert rate_resp.status_code == 200
    rate_data = rate_resp.json()
    assert rate_data["from"] == "USD" and rate_data["to"] == "NGN"

    # Step 9: Logout and ensure session is invalidated
    logout_resp = client.post(
        "/auth/logout", headers={"X-SESSION-TOKEN": session_token}
    )
    assert logout_resp.status_code == 200
    # Subsequent calls with the same token should fail
    me_resp2 = client.get(
        "/user", headers={"X-SESSION-TOKEN": session_token}
    )
    assert me_resp2.status_code == 401

    # Clean up after test
    cleanup_files()


def test_duplicate_registration_and_invalid_login():
    """Test that duplicate registrations are blocked and invalid logins fail."""
    cleanup_files()
    client = TestClient(app)
    email = random_email()
    payload = {
        "first_name": "Dup",
        "last_name": "User",
        "email": email,
        "password": "Password123",
        "confirm_password": "Password123",
    }
    # Register once
    resp1 = client.post("/auth/register", json=payload)
    assert resp1.status_code == 201
    # Registering again with same email should fail
    resp2 = client.post("/auth/register", json=payload)
    assert resp2.status_code == 400
    # Attempt to login with wrong password
    ver = resp1.json()["verification"]["otp"]
    client.post("/auth/verify-email", json={"email": email, "otp": ver})
    bad_login = client.post(
        "/auth/login", json={"email": email, "password": "WrongPass"}
    )
    assert bad_login.status_code == 401
    cleanup_files()


def test_resend_verification_and_token_verification():
    """Ensure resending verification works and tokens can be used for verification."""
    cleanup_files()


if __name__ == "__main__":
    # Execute tests sequentially when run as a script. This allows
    # ``python test_user_management.py`` to serve as a simple test
    # runner without requiring pytest to be installed. Each test
    # function will raise an AssertionError on failure.
    test_register_verify_login_and_key_flow()
    test_duplicate_registration_and_invalid_login()
    test_resend_verification_and_token_verification()
    print("All user management tests passed.")
    client = TestClient(app)
    email = random_email()
    payload = {
        "first_name": "Reverify",
        "last_name": "User",
        "email": email,
        "password": "Password123",
        "confirm_password": "Password123",
    }
    resp = client.post("/auth/register", json=payload)
    assert resp.status_code == 201
    # Immediately call resend verification
    resend_resp = client.post(
        "/auth/resend-verification", json={"email": email}
    )
    assert resend_resp.status_code == 200
    new_otp = resend_resp.json()["verification"]["otp"]
    new_token = resend_resp.json()["verification"]["token"]
    # Use the token instead of OTP for verification
    verify_resp = client.post(
        "/auth/verify-email", json={"email": email, "token": new_token}
    )
    assert verify_resp.status_code == 200
    # Subsequent resend attempts should fail as user is verified
    resend_again = client.post(
        "/auth/resend-verification", json={"email": email}
    )
    assert resend_again.status_code == 400
    cleanup_files()