"""The emails the service sends to users.

Each function composes one message and hands it to ``mailer.send_email``.
They are meant to run as background tasks, after the response.
"""

from __future__ import annotations

from typing import Any, Dict
from urllib.parse import urlencode

from config import PUBLIC_BASE_URL
from mailer import send_email
from services.user_service import VERIFICATION_TTL_SECONDS


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


def send_key_exists(email: str) -> bool:
    """Tell the owner of ``email`` that a new key was requested for it."""
    body = "\n".join(
        [
            "Someone asked for an API key for this email address, but it already has one,",
            "so no new key was issued.",
            "",
            "If it was you, keep using your existing key. If you lost it and it is attached to",
            "an account, sign in and rotate it with POST /user/keys/regenerate-by-prefix.",
            "",
            "If it wasn't you, you can ignore this email. Your key has not changed.",
        ]
    )
    return send_email(email, "You already have an API key", body)


def send_account_exists(email: str) -> bool:
    """Tell the owner of ``email`` that someone tried to register it again."""
    body = "\n".join(
        [
            "Someone tried to create an account with this email address, but it already has one.",
            "",
            "If it was you, log in with your existing password. If you never verified the address,",
            "request a new code from /auth/resend-verification.",
            "",
            "If it wasn't you, you can ignore this email. Nothing about your account has changed.",
        ]
    )
    return send_email(email, "You already have an account", body)
