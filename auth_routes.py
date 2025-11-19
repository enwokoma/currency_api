"""Authentication-related FastAPI routes."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from auth import generate_free_key
from schemas import FreeKeyPayload, RegisterPayload, VerifyEmailPayload, LoginPayload
from users import (
    create_user,
    start_verification,
    verify_user,
    authenticate_user,
    create_session,
    revoke_session,
    sanitize_user,
)

auth_router = APIRouter()


@auth_router.post("/auth/free-key", tags=["Auth"])
def get_free_key(payload: FreeKeyPayload) -> Dict[str, str]:
    """Public endpoint to request a free-tier API key."""
    raw = generate_free_key(
        owner=payload.email.strip().lower(),
        email=payload.email,
        first_name=payload.first_name,
        last_name=payload.last_name,
    )
    return {
        "tier": "free",
        "api_key": raw,
        "note": "Free keys have limited quota. Upgrade for higher limits.",
    }


@auth_router.post("/auth/register", tags=["User"], status_code=201)
def register_user(payload: RegisterPayload) -> Dict[str, Any]:
    """Register a new user."""
    if payload.password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="Password and confirm password do not match")
    user_rec = create_user(
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=payload.email,
        password=payload.password,
    )
    verification = user_rec.get("verification", {}) or {}
    return {
        "message": "User registered successfully. Please verify your email.",
        "user": sanitize_user(user_rec),
        "verification": {
            "otp": verification.get("otp"),
            "token": verification.get("token"),
            "expires_at": verification.get("expires_at"),
        },
    }


@auth_router.post("/auth/resend-verification", tags=["User"])
def resend_verification(email: str) -> Dict[str, Any]:
    """Resend the verification code/link for an unverified user."""
    verification = start_verification(email.strip().lower())
    return {
        "message": "Verification code has been resent.",
        "verification": {
            "otp": verification.get("otp"),
            "token": verification.get("token"),
            "expires_at": verification.get("expires_at"),
        },
    }


@auth_router.post("/auth/verify-email", tags=["User"])
def verify_email(payload: VerifyEmailPayload) -> Dict[str, Any]:
    """Verify a user's email address via OTP or token."""
    if not payload.otp and not payload.token:
        raise HTTPException(status_code=400, detail="Provide either otp or token")
    user = verify_user(
        email=payload.email.strip().lower(),
        otp=payload.otp,
        token=payload.token,
    )
    return {
        "message": "Email verified successfully.",
        "user": sanitize_user(user),
    }


@auth_router.post("/auth/login", tags=["User"])
def login(payload: LoginPayload) -> Dict[str, Any]:
    """Authenticate a user and issue a session token."""
    user = authenticate_user(email=payload.email.strip().lower(), password=payload.password)
    session_rec = create_session(user_id=user["id"])
    return {
        "message": "Login successful.",
        "session_token": session_rec["token"],
        "expires_at": session_rec["expires_at"],
        "user": sanitize_user(user),
    }


@auth_router.post("/auth/logout", tags=["User"])
def logout(request: Request) -> Dict[str, Any]:
    """Revoke an active session token."""
    token = request.headers.get("X-SESSION-TOKEN") or request.query_params.get("session_token")
    if token:
        revoke_session(token)
    return {"message": "Logged out successfully."}
