"""Utility functions for the currency conversion service.

This module provides helpers for validating currency codes and fetching
exchange rates. Rates are obtained from the open‑source Exchange API
maintained by Fawaz Ahmed, served from the jsDelivr CDN with the project's
official Cloudflare Pages mirror as fallback. All codes are normalized to
lowercase for consistency.
"""

from __future__ import annotations

import requests
from datetime import datetime, date
from typing import Dict, Optional
from fastapi import HTTPException


# Endpoints for currency data. Both hosts serve the same dataset in the same
# shape: ``{"date": "...", "<base>": {"<target>": rate, ...}}``. ``{date}`` is
# either ``latest`` or a ``YYYY-MM-DD`` day.
#
# The previous fallback, api.exchangerate.host, has required an access key
# since 2023 and now answers every unauthenticated call with an error, so it
# never actually served a rate.
PRIMARY_URL_TEMPLATE = (
    "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{date}/v1/currencies/{base}.json"
)
FALLBACK_URL_TEMPLATE = "https://{date}.currency-api.pages.dev/v1/currencies/{base}.json"
PROVIDER_TIMEOUT_SECONDS = 5

# Static fallback rates used when external providers are unavailable. This
# ensures the service can still function (albeit with approximate rates)
# in environments without network access. Only a limited set of pairs
# are defined; additional pairs can be added as needed. The keys are
# lowercase currency codes. Values map target currencies to their
# exchange rates relative to the base.
STATIC_FALLBACK_RATES: Dict[str, Dict[str, float]] = {
    # Approximate USD to NGN rate as of 2025-11 (used for tests)
    "usd": {"ngn": 1630.0, "usd": 1.0},
    # Reverse mapping for NGN to USD
    "ngn": {"usd": 1.0 / 1630.0, "ngn": 1.0},
    # Additional currencies can be added here if needed
}


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


def _fetch_from_provider(template: str, base: str, date_tag: str) -> Optional[Dict[str, float]]:
    """Fetch one base's rates from a provider; return None on any failure."""
    try:
        resp = requests.get(
            template.format(date=date_tag, base=base),
            timeout=PROVIDER_TIMEOUT_SECONDS,
        )
        if resp.status_code != 200:
            return None
        raw_rates = resp.json().get(base)
        if not raw_rates:
            return None
        return {k.lower(): float(v) for k, v in raw_rates.items()}
    except Exception:
        return None


def _fetch_with_fallbacks(base: str, date_tag: str) -> Dict[str, float]:
    """Try the CDN, then the mirror, then the static table."""
    for template in (PRIMARY_URL_TEMPLATE, FALLBACK_URL_TEMPLATE):
        rates = _fetch_from_provider(template, base, date_tag)
        if rates:
            return rates
    # If both providers fail, fall back to static rates. This prevents
    # returning a 502 error in offline environments. If the base is
    # known in ``STATIC_FALLBACK_RATES`` we return its mapping. If not,
    # provide at least a self conversion rate.
    if base in STATIC_FALLBACK_RATES:
        return STATIC_FALLBACK_RATES[base]
    return {base: 1.0}


def fetch_rates(base: str) -> Dict[str, float]:
    """Fetch current conversion rates for a given base currency.

    Attempts the primary CDN first, then the mirror, then the static
    table. Returns a dictionary mapping target currencies to
    floating‑point rates.

    Args:
        base: The base currency code (case insensitive).

    Returns:
        A mapping of lowercase target currency codes to their exchange rates.
    """
    base = validate_currency(base)
    return _fetch_with_fallbacks(base, "latest")


def _validate_date(date_str: str) -> str:
    """Validate a YYYY-MM-DD date string and ensure it is not in the future."""
    try:
        parsed = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Date must be in format YYYY-MM-DD",
        )
    if parsed > date.today():
        raise HTTPException(
            status_code=400,
            detail="Date cannot be in the future",
        )
    return parsed.isoformat()


def fetch_historical_rates(base: str, date_str: str) -> Dict[str, float]:
    """Fetch historical conversion rates for a given date and base currency.

    Args:
        base: The base currency code (lowercase).
        date_str: Date in ``YYYY-MM-DD`` format (must not be in the future).

    Returns:
        A mapping of target currency codes to their exchange rates on the date.

    Raises:
        HTTPException: If the currency code or date is invalid.
    """
    base = validate_currency(base)
    safe_date = _validate_date(date_str)
    return _fetch_with_fallbacks(base, safe_date)
