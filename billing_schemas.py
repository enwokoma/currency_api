"""Pydantic schemas used by billing routes."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from schemas import KeyContact


class SubscribePayload(KeyContact):
    """Direct paid subscription payload (without requiring an existing key)."""

    plan_code: str
    owner: Optional[str] = None  # Optional identifier (e.g., user id) to tag the key


class UpgradePayload(BaseModel):
    plan_code: str
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None


class VerifyTransactionPayload(BaseModel):
    reference: str
