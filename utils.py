"""Utility functions for the currency conversion service.

This module provides helpers for validating currency codes and fetching
exchange rates. Rates are obtained from the open‑source Exchange API
maintained by Fawaz Ahmed with a fallback to exchangerate.host. All
codes are normalized to lowercase for consistency.
"""

from __future__ import annotations

import requests
from typing import Dict
from fastapi import HTTPException


# Endpoints for currency data
PRIMARY_BASE_URL = (
    "https://cdn.jsdelivr.net/npm/"
    "@fawazahmed0/currency-api@latest/v1/currencies"
)
FALLBACK_URL_TEMPLATE = "https://api.exchangerate.host/latest?base={base}"


def validate_currency(code: str) -> str:
    """Validate and normalize a three‑letter currency code.

    Args:
        code: A currency code (case insensitive).

    Returns:
        The lowercase currency code if valid.

    Raises:
        HTTPException: If the code is not exactly three alphabetic
            characters.
    """
    code = code.strip().lower()
    if len(code) != 3 or not code.isalpha():
        raise HTTPException(
            status_code=400,
            detail="Currency code must be 3 letters (A–Z)",
        )
    return code


def fetch_rates(base: str) -> Dict[str, float]:
    """Fetch conversion rates for a given base currency.

    Attempts to fetch from the primary provider first. If that fails,
    falls back to exchangerate.host. Returns a dictionary mapping
    target currencies to floating‑point rates.

    Args:
        base: The base currency code (lowercase).

    Returns:
        A mapping of target currency codes to their exchange rates.

    Raises:
        HTTPException: If neither provider responds successfully.
    """
    base = validate_currency(base)

    # Try the primary provider
    try:
        url = f"{PRIMARY_BASE_URL}/{base}.json"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            raw_rates = data.get(base)
            if raw_rates:
                return {k.lower(): float(v) for k, v in raw_rates.items()}
    except Exception:
        pass

    # Fallback provider
    try:
        url = FALLBACK_URL_TEMPLATE.format(base=base.upper())
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            raw_rates = data.get("rates")
            if raw_rates:
                return {k.lower(): float(v) for k, v in raw_rates.items()}
    except Exception:
        pass

    raise HTTPException(
        status_code=502,
        detail="Unable to fetch exchange rates from providers at this time.",
    )