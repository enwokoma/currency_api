"""Centralised configuration and plan definitions for the API.

This module keeps environment-derived plan codes and subscription limits
in one place so that other modules can import them without bloating
their own scope or repeating environment lookups.
"""

from __future__ import annotations

import os
from typing import List, Optional

import env  # noqa: F401  # ensures .env is loaded before reading os.getenv

# Load Paystack plan codes from environment. These environment
# variables should be defined in your .env file or the deployment
# environment. They map our internal plan codes to the codes defined
# on your Paystack dashboard.
PAYSTACK_PLAN_BASIC = os.getenv("PAYSTACK_PLAN_BASIC")
PAYSTACK_PLAN_PRO = os.getenv("PAYSTACK_PLAN_PRO")

PLANS = [
    {
        "code": "BASIC_MONTHLY",
        "name": "Basic Monthly",
        "price_naira": 2000,
        "currency": "NGN",
        "requests_per_minute": 100,
        "requests_per_day": 10_000,
        "monthly_credits": 5000,
        "paystack_plan_code": PAYSTACK_PLAN_BASIC,
    },
    {
        "code": "PRO_MONTHLY",
        "name": "Pro Monthly",
        "price_naira": 5000,
        "currency": "NGN",
        "requests_per_minute": 300,
        "requests_per_day": 50_000,
        "monthly_credits": 20_000,
        "paystack_plan_code": PAYSTACK_PLAN_PRO,
    },
]

# Free-tier limits and credits
FREE_REQUESTS_PER_MINUTE = 10
FREE_REQUESTS_PER_DAY = 200
FREE_TIER_MONTHLY_CREDITS = 200

# Per-key limit on billing calls (/billing/upgrade, /billing/verify,
# /billing/cancel, /transactions). They cost no credits, so without a limit a
# key could hammer them, and /billing/upgrade can start Paystack checkouts.
BILLING_REQUESTS_PER_MINUTE = 20
BILLING_REQUESTS_PER_DAY = 500

# Paystack webhook: largest body accepted, and how many refused deliveries
# (bad signature, too large) are recorded per client (per minute, per day).
# Refused deliveries are logged as size and reason only, never their body.
WEBHOOK_MAX_BODY_BYTES = 256 * 1024
WEBHOOK_REJECTED_LOG_LIMITS = (10, 200)

# Per-account sign-in lockout: after this many wrong passwords for one email
# within the window, every password check for that email (a correct one
# included) gets 429 until older failures age out. Unknown emails are counted
# the same way, so the lockout reveals nothing. Trade-off: anyone can lock an
# account for the window by failing on purpose; per-client limits slow that.
LOGIN_MAX_FAILURES = 10
LOGIN_LOCKOUT_SECONDS = 15 * 60

# Per-client limits for routes that take no credential, as (requests per
# minute, requests per day) per route and client address. They slow password
# and verification-code guessing, bulk key minting and checkout spam. The
# client address is the TCP peer: behind a reverse proxy, run uvicorn with
# --proxy-headers and --forwarded-allow-ips=<proxy address> so each client is
# counted on its own instead of everyone sharing the proxy's budget.
PUBLIC_ROUTE_LIMITS = {
    "free-key": (5, 50),
    "register": (5, 50),
    "login": (10, 300),
    "verify-email": (10, 100),
    "resend-verification": (3, 30),
    "subscribe": (5, 50),
    "forgot-password": (3, 30),
    "reset-password": (10, 100),
}

# Public address of this API (e.g. "https://api.example.com"), used to build
# links in emails. Without it, emails carry codes but no links.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")


# Outgoing email (see mailer.py). MAIL_BACKEND is "smtp" (default) or
# "console"; console prints messages, secrets included, to stdout and is for
# local development only.
MAIL_BACKEND = os.getenv("MAIL_BACKEND", "smtp").strip().lower()
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_SECURITY = os.getenv("SMTP_SECURITY", "starttls").strip().lower()  # starttls | ssl | none
SMTP_PORT = int(os.getenv("SMTP_PORT") or (465 if SMTP_SECURITY == "ssl" else 587))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "").strip()
SMTP_TIMEOUT_SECONDS = 15
# At most this many messages per recipient address (per minute, per day), so
# public endpoints that send mail cannot be used to flood someone's inbox.
MAIL_PER_RECIPIENT_LIMITS = (3, 20)

def parse_cors_origins(raw: Optional[str]) -> List[str]:
    """Split a comma-separated origin list; unset or blank means any origin."""
    origins = [origin.strip() for origin in (raw or "").split(",") if origin.strip()]
    return origins or ["*"]


# Origins allowed to call the API from a browser (comma-separated), e.g.
# "https://app.example.com,https://admin.example.com". Defaults to any origin,
# which is safe because cross-origin requests never carry credentials: every
# credential is a header the calling page must set itself, never a cookie.
CORS_ALLOW_ORIGINS = parse_cors_origins(os.getenv("CORS_ALLOW_ORIGINS"))

# Default paid limits for keys that are tier='paid' but have no plan_code
# These limits are applied to admin-generated keys and any paid key that
# is not yet subscribed to a plan. They reflect the baseline of our
# Basic plan (100 calls per minute, 10k per day).
DEFAULT_PAID_REQUESTS_PER_MINUTE = 100
DEFAULT_PAID_REQUESTS_PER_DAY = 10_000
