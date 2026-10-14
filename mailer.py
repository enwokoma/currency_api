"""Outgoing email for verification codes, API keys and account notices.

Settings live in ``config.py`` and come from the environment: ``MAIL_BACKEND``
(``smtp`` or ``console``), ``SMTP_HOST``, ``SMTP_PORT``, ``SMTP_SECURITY``
(``starttls``, ``ssl`` or ``none``), ``SMTP_USERNAME``, ``SMTP_PASSWORD`` and
``SMTP_FROM``.

Routes send mail from FastAPI background tasks, after the response has gone
out, so a slow or failing mail server never changes what the caller sees.
``send_email`` therefore never raises: it reports failures in the log and
returns False.
"""

from __future__ import annotations

import re
import smtplib
import ssl
from email.message import EmailMessage

from config import (
    MAIL_BACKEND,
    MAIL_PER_RECIPIENT_LIMITS,
    SMTP_FROM,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_SECURITY,
    SMTP_TIMEOUT_SECONDS,
    SMTP_USERNAME,
)
from rate_limit import try_acquire

# Deliberately simple: one "@", no whitespace or control characters, and a
# dotted domain. Enough to refuse junk and header injection before SMTP.
_ADDRESS_RE = re.compile(r"[^@\s\x00-\x1f\x7f]+@[^@\s\x00-\x1f\x7f.]+(\.[^@\s\x00-\x1f\x7f.]+)+")


def valid_address(address: str | None) -> bool:
    """True when ``address`` looks like a deliverable email address."""
    return bool(address) and len(address) <= 254 and _ADDRESS_RE.fullmatch(address) is not None


def is_configured() -> bool:
    """True when messages can actually leave this process."""
    if MAIL_BACKEND == "console":
        return True
    return MAIL_BACKEND == "smtp" and bool(SMTP_HOST and SMTP_FROM)


def _mask(address: str) -> str:
    """Hide the local part of an address for log lines."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}"


def _smtp_deliver(message: EmailMessage) -> None:
    context = ssl.create_default_context()
    if SMTP_SECURITY == "ssl":
        client = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS, context=context)
    else:
        client = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS)
    with client:
        if SMTP_SECURITY == "starttls":
            client.starttls(context=context)
        if SMTP_USERNAME:
            client.login(SMTP_USERNAME, SMTP_PASSWORD)
        client.send_message(message)


def _console_deliver(message: EmailMessage) -> None:
    print(f"[mail:console]\n{message.as_string()}")


def _deliver(message: EmailMessage) -> None:
    """Hand a message to the configured backend (tests replace this)."""
    if MAIL_BACKEND == "console":
        _console_deliver(message)
    elif is_configured():
        _smtp_deliver(message)
    else:
        raise RuntimeError("SMTP is not configured (SMTP_HOST and SMTP_FROM)")


def send_email(to: str, subject: str, body: str) -> bool:
    """Send a plain-text message. Returns True when it was handed off."""
    if not valid_address(to):
        print("[mail] Not sent, invalid recipient address")
        return False
    per_minute, per_day = MAIL_PER_RECIPIENT_LIMITS
    if not try_acquire(f"mail:{to.lower()}", per_minute, per_day):
        print(f"[mail] Not sent, per-recipient limit reached for {_mask(to)}")
        return False
    message = EmailMessage()
    message["From"] = SMTP_FROM or "no-reply@localhost"
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    try:
        _deliver(message)
    except Exception as exc:  # background task: report, never raise
        print(f"[mail] Failed to send {subject!r} to {_mask(to)}: {exc.__class__.__name__}: {exc}")
        return False
    return True
