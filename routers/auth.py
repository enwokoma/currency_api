"""Authentication-related FastAPI routes."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Query

from credential_utils import header_credential
from dependencies import public_route_limit
from mailer import valid_address
from notifications import send_account_exists, send_verification
from services.auth_service import generate_free_key, find_key_by_email
from schemas import (
    FreeKeyPayload,
    RegisterPayload,
    ResendVerificationPayload,
    VerifyEmailPayload,
    LoginPayload,
)
from services.user_service import (
    create_user,
    start_verification,
    verify_user,
    authenticate_user,
    create_session,
    revoke_session,
    sanitize_user,
)

auth_router = APIRouter()


@auth_router.post(
    "/auth/free-key",
    tags=["Auth"],
    dependencies=[Depends(public_route_limit("free-key"))],
)
def get_free_key(payload: FreeKeyPayload) -> Dict[str, str]:
    """Public endpoint to request a free-tier API key."""
    email_norm = payload.email.strip().lower()
    existing = find_key_by_email(email_norm, include_inactive=True, include_revoked=True)
    if existing:
        raise HTTPException(
            status_code=400,
            detail="A key already exists for this email. Please check your inbox or use /auth/register to manage keys.",
        )
    raw = generate_free_key(
        owner=email_norm,
        email=email_norm,
        first_name=payload.first_name,
        last_name=payload.last_name,
    )
    return {
        "tier": "free",
        "api_key": raw,
        "note": "Free keys have limited quota. Upgrade for higher limits.",
    }


@auth_router.post(
    "/auth/register",
    tags=["User"],
    status_code=202,
    dependencies=[Depends(public_route_limit("register"))],
)
def register_user(payload: RegisterPayload, background: BackgroundTasks) -> Dict[str, Any]:
    """Register a new user and email them a verification code.

    The answer is the same whether or not the email already has an account;
    the owner of an existing account gets a notice by email instead. The code
    proves control of the inbox, so it is never part of the response.
    """
    if payload.password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="Password and confirm password do not match")
    email = payload.email.strip().lower()
    if not valid_address(email):
        raise HTTPException(status_code=400, detail="Enter a valid email address")
    user_rec = create_user(
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=email,
        password=payload.password,
    )
    if user_rec is None:
        background.add_task(send_account_exists, email)
    else:
        background.add_task(send_verification, email, user_rec["verification"])
    return {"message": "Registration received. Check your email to verify your address."}


@auth_router.post(
    "/auth/resend-verification",
    tags=["User"],
    dependencies=[Depends(public_route_limit("resend-verification"))],
)
def resend_verification(
    payload: ResendVerificationPayload,
    background: BackgroundTasks,
) -> Dict[str, Any]:
    """Email a fresh verification code/link to an unverified user.

    Answers the same for unknown, unverified and verified addresses.
    """
    email = payload.email.strip().lower()
    verification = start_verification(email)
    if verification is not None:
        background.add_task(send_verification, email, verification)
    return {"message": "If this address has an unverified account, a new code is on its way."}


@auth_router.post(
    "/auth/verify-email",
    tags=["User"],
    dependencies=[Depends(public_route_limit("verify-email"))],
)
def verify_email(payload: VerifyEmailPayload) -> Dict[str, Any]:
    """Verify a user's email address via OTP or token."""
    if bool(payload.otp) == bool(payload.token):
        raise HTTPException(status_code=400, detail="Provide either otp or token (but not both)")
    user = verify_user(
        email=payload.email.strip().lower(),
        otp=payload.otp,
        token=payload.token,
    )
    return {
        "message": "Email verified successfully.",
        "user": sanitize_user(user),
    }


@auth_router.get(
    "/auth/verify-email",
    tags=["User"],
    dependencies=[Depends(public_route_limit("verify-email"))],
)
def verify_email_via_link(
    email: str = Query(..., description="Email address to verify"),
    token: str = Query(..., description="Verification token from email link"),
) -> Dict[str, Any]:
    """Verify a user's email via direct link (token + email query params)."""
    user = verify_user(
        email=email.strip().lower(),
        token=token,
    )
    return {
        "message": "Email verified successfully.",
        "user": sanitize_user(user),
    }


@auth_router.post(
    "/auth/login",
    tags=["User"],
    dependencies=[Depends(public_route_limit("login"))],
)
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
    token = header_credential(request, "X-SESSION-TOKEN", "session_token")
    if token:
        revoke_session(token)
    return {"message": "Logged out successfully."}
