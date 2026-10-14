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
}

# Local development only: echo email verification codes (OTP and link token)
# in the /auth/register and /auth/resend-verification responses, for setups
# with no mailer. Never enable it on a reachable deployment: anyone could then
# verify any email address they register.
DEV_RETURN_VERIFICATION_CODES = os.getenv("DEV_RETURN_VERIFICATION_CODES", "").strip().lower() in {
    "1",
    "true",
    "yes",
}


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
