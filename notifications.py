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
