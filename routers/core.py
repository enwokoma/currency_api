"""Core currency conversion routes."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dependencies import api_key_required, paid_key_required
from services.rate_service import (
    fetch_historical_rates,
    fetch_rates,
    validate_currency,
)

core_router = APIRouter()


class RateRequest(BaseModel):
    from_currency: str
    to_currency: str


class ConvertRequest(RateRequest):
    amount: float


class BaseRatesRequest(BaseModel):
    base_currency: str


class HistoricalRateRequest(RateRequest):
    date: str


class HistoricalConvertRequest(HistoricalRateRequest):
    amount: float


class HistoricalBaseRatesRequest(BaseModel):
    base_currency: str
    date: str


@core_router.get("/health", tags=["Utility"])
def health() -> Dict[str, str]:
    """Basic health check."""
    return {"status": "ok"}


@core_router.post("/rates", tags=["Core"], response_model=Dict[str, Any])
def get_all_rates(
    payload: BaseRatesRequest,
    _: Dict[str, Any] = Depends(api_key_required),
) -> Dict[str, Any]:
    """Return all current rates for a base currency."""
    base_lower = validate_currency(payload.base_currency)
    rates = fetch_rates(base_lower)
    return {
        "base": payload.base_currency.upper(),
        "rates": rates,
    }


@core_router.post("/rate", tags=["Core"], response_model=Dict[str, Any])
def get_rate(
    payload: RateRequest,
    _: Dict[str, Any] = Depends(api_key_required),
) -> Dict[str, Any]:
    """Retrieve the conversion rate for one currency pair."""
    from_lower = validate_currency(payload.from_currency)
    to_lower = validate_currency(payload.to_currency)
    rates = fetch_rates(from_lower)
    if to_lower not in rates:
        raise HTTPException(
            status_code=400,
            detail=f"Target currency '{payload.to_currency.upper()}' is not supported",
        )
    return {
        "from": payload.from_currency.upper(),
        "to": payload.to_currency.upper(),
        "rate": rates[to_lower],
    }


@core_router.post("/convert", tags=["Core"], response_model=Dict[str, Any])
def convert_currency(
    payload: ConvertRequest,
    _: Dict[str, Any] = Depends(api_key_required),
) -> Dict[str, Any]:
    """Convert an amount from one currency to another."""
    if payload.amount < 0:
        raise HTTPException(status_code=400, detail="Amount must be non-negative")
    from_lower = validate_currency(payload.from_currency)
    to_lower = validate_currency(payload.to_currency)
    rates = fetch_rates(from_lower)
    if to_lower not in rates:
        raise HTTPException(
            status_code=400,
            detail=f"Target currency '{payload.to_currency.upper()}' is not supported",
        )
    rate = rates[to_lower]
    converted_amount = round(payload.amount * rate, 4)
    return {
        "from": payload.from_currency.upper(),
        "to": payload.to_currency.upper(),
        "amount": payload.amount,
        "rate": rate,
        "converted_amount": converted_amount,
    }


@core_router.post("/rates/historical", tags=["Core"], response_model=Dict[str, Any])
def get_historical_rates_endpoint(
    payload: HistoricalBaseRatesRequest,
    _: Dict[str, Any] = Depends(paid_key_required),
) -> Dict[str, Any]:
    """Retrieve all rates for a base currency on a specific date."""
    base_lower = validate_currency(payload.base_currency)
    rates = fetch_historical_rates(base_lower, payload.date)
    return {
        "base": payload.base_currency.upper(),
        "date": payload.date,
        "rates": rates,
    }


@core_router.post("/rate/historical", tags=["Core"], response_model=Dict[str, Any])
def get_historical_rate(
    payload: HistoricalRateRequest,
    _: Dict[str, Any] = Depends(paid_key_required),
) -> Dict[str, Any]:
    """Retrieve a historical conversion rate between two currencies."""
    from_lower = validate_currency(payload.from_currency)
    to_lower = validate_currency(payload.to_currency)
    rates = fetch_historical_rates(from_lower, payload.date)
    if to_lower not in rates:
        raise HTTPException(
            status_code=400,
            detail=f"Target currency '{payload.to_currency.upper()}' is not supported",
        )
    return {
        "from": payload.from_currency.upper(),
        "to": payload.to_currency.upper(),
        "date": payload.date,
        "rate": rates[to_lower],
    }


@core_router.post("/convert/historical", tags=["Core"], response_model=Dict[str, Any])
def convert_currency_historical(
    payload: HistoricalConvertRequest,
    _: Dict[str, Any] = Depends(paid_key_required),
) -> Dict[str, Any]:
    """Convert an amount between two currencies on a specific date."""
    if payload.amount < 0:
        raise HTTPException(status_code=400, detail="Amount must be non-negative")
    from_lower = validate_currency(payload.from_currency)
    to_lower = validate_currency(payload.to_currency)
    rates = fetch_historical_rates(from_lower, payload.date)
    if to_lower not in rates:
        raise HTTPException(
            status_code=400,
            detail=f"Target currency '{payload.to_currency.upper()}' is not supported",
        )
    rate = rates[to_lower]
    converted_amount = round(payload.amount * rate, 4)
    return {
        "from": payload.from_currency.upper(),
        "to": payload.to_currency.upper(),
        "date": payload.date,
        "amount": payload.amount,
        "rate": rate,
        "converted_amount": converted_amount,
    }
