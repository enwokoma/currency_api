"""
User management utilities for the currency conversion API.

This module provides basic user persistence, password hashing,
verification token generation and short-lived session tokens. The
implementation intentionally uses JSON files so the service can run
without a database. All helper functions are synchronous and safe to
call from FastAPI endpoints.
"""

from __future__ import annotations

import secrets
import time
import uuid
import hashlib
from typing import Dict, Any, Optional, Tuple

from fastapi import HTTPException, status
from storage_utils import atomic_write_json, ensure_json_file, load_json_file


USERS_FILE = "users.json"
SESSIONS_FILE = "sessions.json"

# Verification codes expire after 15 minutes
VERIFICATION_TTL_SECONDS = 15 * 60
# Sessions last for 24 hours by default
SESSION_TTL_SECONDS = 24 * 60 * 60


def _ensure_file(path: str, root_key: str) -> None:
    """Ensure a JSON file exists with the given root key."""
    ensure_json_file(path, root_key)


def _load_json(path: str, root_key: str) -> Dict[str, Any]:
    return load_json_file(path, root_key)


def _save_json(path: str, data: Dict[str, Any]) -> None:
    atomic_write_json(path, data)


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _hash_password(password: str, salt_hex: Optional[str] = None) -> Tuple[str, str]:
    """Hash a password using PBKDF2-HMAC SHA256 with a per-user salt."""
    if salt_hex:
        salt = bytes.fromhex(salt_hex)
    else:
        salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return salt.hex(), derived.hex()


def _generate_verification_bundle() -> Dict[str, Any]:
    """Create a fresh OTP/token combo for email verification."""
    otp = f"{secrets.randbelow(1_000_000):06d}"
    token = f"verify_{secrets.token_urlsafe(32)}"
    expires_at = time.time() + VERIFICATION_TTL_SECONDS
    return {"otp": otp, "token": token, "expires_at": expires_at}


def load_users() -> Dict[str, Any]:
    _ensure_file(USERS_FILE, "users")
    return _load_json(USERS_FILE, "users")


def save_users(data: Dict[str, Any]) -> None:
    _save_json(USERS_FILE, data)


def load_sessions() -> Dict[str, Any]:
    _ensure_file(SESSIONS_FILE, "sessions")
    return _load_json(SESSIONS_FILE, "sessions")


def save_sessions(data: Dict[str, Any]) -> None:
    _save_json(SESSIONS_FILE, data)


def _find_user_index(email: str, data: Dict[str, Any]) -> Optional[int]:
    email_lower = email.lower()
    for idx, user in enumerate(data.get("users", [])):
        if user.get("email") == email_lower:
            return idx
    return None


def normalize_email(email: str) -> str:
    return email.strip().lower()


def create_user(first_name: str, last_name: str, email: str, password: str) -> Dict[str, Any]:
    data = load_users()
    email_norm = normalize_email(email)
    if _find_user_index(email_norm, data) is not None:
        raise HTTPException(status_code=400, detail="Email is already registered")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters long")
    user_id = uuid.uuid4().hex
    salt_hex, password_hash = _hash_password(password)
    verification = _generate_verification_bundle()
    user_record = {
        "id": user_id,
        "first_name": first_name.strip(),
        "last_name": last_name.strip(),
        "email": email_norm,
        "password_hash": password_hash,
        "salt": salt_hex,
        "email_verified": False,
        "verification": verification,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "last_login_at": None,
    }
    data.setdefault("users", []).append(user_record)
    save_users(data)
    return user_record


def sanitize_user(user: Dict[str, Any]) -> Dict[str, Any]:
    """Return a safe representation that omits secrets."""
    fields = {
        "id": user.get("id"),
        "first_name": user.get("first_name"),
        "last_name": user.get("last_name"),
        "email": user.get("email"),
        "email_verified": user.get("email_verified", False),
        "created_at": user.get("created_at"),
        "updated_at": user.get("updated_at"),
        "last_login_at": user.get("last_login_at"),
    }
    return fields


def _assert_user_exists(email: str) -> Dict[str, Any]:
    data = load_users()
    idx = _find_user_index(email, data)
    if idx is None:
        raise HTTPException(status_code=404, detail="User not found")
    return data["users"][idx]


def start_verification(email: str) -> Dict[str, Any]:
    """Regenerate verification bundle for a user."""
    data = load_users()
    idx = _find_user_index(email, data)
    if idx is None:
        raise HTTPException(status_code=404, detail="User not found")
    if data["users"][idx].get("email_verified"):
        raise HTTPException(status_code=400, detail="Email is already verified")
    verification = _generate_verification_bundle()
    data["users"][idx]["verification"] = verification
    data["users"][idx]["updated_at"] = _now_iso()
    save_users(data)
    return verification


def verify_user(email: str, otp: Optional[str] = None, token: Optional[str] = None) -> Dict[str, Any]:
    """Mark the user's email as verified if the provided OTP or token matches."""
    if not otp and not token:
        raise HTTPException(status_code=400, detail="Provide either otp or token")
    data = load_users()
    idx = _find_user_index(email, data)
    if idx is None:
        raise HTTPException(status_code=404, detail="User not found")
    user = data["users"][idx]
    if user.get("email_verified"):
        return user
    verification = user.get("verification")
    if not verification:
        raise HTTPException(status_code=400, detail="No verification challenge is pending")
    if time.time() > verification.get("expires_at", 0):
        raise HTTPException(status_code=400, detail="Verification code expired")
    otp_valid = bool(otp and verification.get("otp") == otp)
    token_valid = bool(token and verification.get("token") == token)
    if not (otp_valid or token_valid):
        detail = "Invalid OTP" if otp and not token else "Invalid verification link"
        raise HTTPException(status_code=400, detail=detail)
    user["email_verified"] = True
    user["verification"] = None
    user["updated_at"] = _now_iso()
    data["users"][idx] = user
    save_users(data)
    return user


def authenticate_user(email: str, password: str) -> Dict[str, Any]:
    """Return the user if the email/password combination is valid."""
    data = load_users()
    idx = _find_user_index(email, data)
    if idx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    user = data["users"][idx]
    salt_hex = user.get("salt")
    if not salt_hex:
        raise HTTPException(status_code=500, detail="User salt is missing")
    _, candidate_hash = _hash_password(password, salt_hex)
    if candidate_hash != user.get("password_hash"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.get("email_verified"):
        raise HTTPException(status_code=403, detail="Email is not verified yet")
    user["last_login_at"] = _now_iso()
    data["users"][idx] = user
    save_users(data)
    return user


def create_session(user_id: str) -> Dict[str, Any]:
    """Create a new session token for the given user id."""
    data = load_sessions()
    token = f"session_{secrets.token_urlsafe(32)}"
    now_iso = _now_iso()
    session_record = {
        "token": token,
        "user_id": user_id,
        "created_at": now_iso,
        "expires_at": time.time() + SESSION_TTL_SECONDS,
        "revoked": False,
    }
    data.setdefault("sessions", []).append(session_record)
    save_sessions(data)
    return session_record


def get_session(token: str) -> Optional[Dict[str, Any]]:
    data = load_sessions()
    for session in data.get("sessions", []):
        if session.get("token") == token and not session.get("revoked", False):
            return session
    return None


def revoke_session(token: str) -> None:
    data = load_sessions()
    updated = False
    for session in data.get("sessions", []):
        if session.get("token") == token and not session.get("revoked", False):
            session["revoked"] = True
            updated = True
    if updated:
        save_sessions(data)


def get_user_by_session(token: str, require_verified: bool = True) -> Dict[str, Any]:
    session = get_session(token)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    if time.time() > session.get("expires_at", 0):
        revoke_session(token)
        raise HTTPException(status_code=401, detail="Session expired")
    data = load_users()
    for user in data.get("users", []):
        if user.get("id") == session.get("user_id"):
            if require_verified and not user.get("email_verified"):
                raise HTTPException(status_code=403, detail="Email is not verified yet")
            return user
    raise HTTPException(status_code=401, detail="User for this session was not found")


def list_users() -> Dict[str, Any]:
    """Helper mainly for tests/admins."""
    return load_users()
