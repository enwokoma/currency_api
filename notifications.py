"""The emails the service sends to users.

Each function composes one message and hands it to ``mailer.send_email``.
They are meant to run as background tasks, after the response.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import urlencode

from config import PUBLIC_BASE_URL
from mailer import send_email
from services.user_service import PASSWORD_RESET_TTL_SECONDS, VERIFICATION_TTL_SECONDS


def send_verification(email: str, verification: Dict[str, Any]) -> bool:
    """Email the code (and link) that proves control of ``email``."""
    otp = verification.get("otp")
    token = verification.get("token")
    lines = [
        "Use this code to verify your email address:",
        "",
        f"Verification code: {otp}",
        f"Verification token: {token}",
        "",
    ]
    if PUBLIC_BASE_URL:
        query = urlencode({"email": email, "token": token})
        lines += ["Or open this link:", f"{PUBLIC_BASE_URL}/auth/verify-email?{query}", ""]
    lines.append(
        f"The code expires in {VERIFICATION_TTL_SECONDS // 60} minutes. "
        "If you did not sign up, you can ignore this email."
    )
    return send_email(email, "Verify your email address", "\n".join(lines))


def send_api_key(email: str, raw_key: str, tier: str) -> bool:
    """Email a newly issued API key to its contact address."""
    body = "\n".join(
        [
            f"Here is your {tier}-tier API key:",
            "",
            f"API key: {raw_key}",
            "",
            "Send it in the X-API-KEY header. Keep it secret: it is shown only in this email.",
            "To manage the key from an account, register and attach it with POST /user/keys/claim.",
        ]
    )
    return send_email(email, "Your API key", body)


def send_key_exists(email: str, plan_name: Optional[str] = None) -> bool:
    """Tell the owner of ``email`` that a new key was requested for it."""
    lines = [
        "Someone asked for an API key for this email address, but it already has one,",
        "so no new key was issued.",
        "",
        "If it was you, keep using your existing key. If you lost it and it is attached to",
        "an account, sign in and rotate it with POST /user/keys/regenerate-by-prefix.",
    ]
    if plan_name:
        lines += ["", f"To move that key to {plan_name}, call POST /billing/upgrade with it."]
    lines += ["", "If it wasn't you, you can ignore this email. Your key has not changed."]
    return send_email(email, "You already have an API key", "\n".join(lines))


def send_subscription_checkout(
    email: str, raw_key: str, plan: Dict[str, Any], authorization_url: str, reference: str
) -> bool:
    """Email a new paid key with the Paystack link that activates it."""
    body = "\n".join(
        [
            f"Here is your API key for {plan['name']}:",
            "",
            f"API key: {raw_key}",
            "",
            "It stays inactive until payment completes. Pay here:",
            "",
            f"Checkout link: {authorization_url}",
            f"Reference: {reference}",
            "",
            "Send the key in the X-API-KEY header. Keep it secret: it is shown only in this email.",
        ]
    )
    return send_email(email, f"Finish subscribing to {plan['name']}", body)


def send_subscription_failed(email: str, plan: Dict[str, Any]) -> bool:
    """Tell ``email`` that checkout could not be started, so nothing was issued."""
    body = "\n".join(
        [
            f"We could not start checkout for {plan['name']}, so no key was issued and you",
            "have not been charged. Please try subscribing again in a few minutes.",
        ]
    )
    return send_email(email, f"Subscription to {plan['name']} not started", body)


def send_account_exists(email: str) -> bool:
    """Tell the owner of ``email`` that someone tried to register it again."""
    body = "\n".join(
        [
            "Someone tried to create an account with this email address, but it already has one.",
            "",
            "If it was you, log in with your existing password, or reset it with",
            "POST /auth/forgot-password. If you never verified the address, request a new code",
            "from /auth/resend-verification.",
            "",
            "If it wasn't you, you can ignore this email. Nothing about your account has changed.",
        ]
    )
    return send_email(email, "You already have an account", body)


def send_password_reset(email: str, token: str) -> bool:
    """Email the token that lets the owner of ``email`` set a new password."""
    body = "\n".join(
        [
            "Someone asked to reset the password for this account.",
            "",
            f"Reset token: {token}",
            "",
            "Send it to POST /auth/reset-password with your email and a new password.",
            f"It expires in {PASSWORD_RESET_TTL_SECONDS // 60} minutes and works once.",
            "",
            "If it wasn't you, ignore this email. Your password has not changed.",
        ]
    )
    return send_email(email, "Reset your password", body)


def send_password_changed(email: str) -> bool:
    """Tell the owner of ``email`` that the password was just reset."""
    body = "\n".join(
        [
            "The password for this account was just reset, and every signed-in session",
            "was signed out.",
            "",
            "If it wasn't you, reset it again at once with POST /auth/forgot-password,",
            "and check who can read this mailbox.",
        ]
    )
    return send_email(email, "Your password was changed", body)
