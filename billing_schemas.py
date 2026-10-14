"""Pydantic schemas used by billing routes."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from schemas import KeyContact


class SubscribePayload(KeyContact):
    """Direct paid subscription payload (without requiring an existing key).

    The key belongs to its email. To manage it from an account, attach it
    with POST /user/keys/claim, which requires the raw key.
    """

    plan_code: str


class UpgradePayload(BaseModel):
    plan_code: str
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None


class VerifyTransactionPayload(BaseModel):
    reference: str
