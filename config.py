"""Centralised configuration and plan definitions for the API.

This module keeps environment-derived plan codes and subscription limits
in one place so that other modules can import them without bloating
their own scope or repeating environment lookups.
"""

from __future__ import annotations

import os

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

# Default paid limits for keys that are tier='paid' but have no plan_code
# These limits are applied to admin-generated keys and any paid key that
# is not yet subscribed to a plan. They reflect the baseline of our
# Basic plan (100 calls per minute, 10k per day).
DEFAULT_PAID_REQUESTS_PER_MINUTE = 100
DEFAULT_PAID_REQUESTS_PER_DAY = 10_000
