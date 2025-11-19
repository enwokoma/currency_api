"""Shared Pydantic schemas for auth, user and admin flows."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class KeyContact(BaseModel):
    email: str
    first_name: str
    last_name: str


class OwnerPayload(KeyContact):
    owner: str
    plan_code: Optional[str] = None
    activate: bool = True


class RevokePayload(BaseModel):
    api_key: str


class RestorePayload(BaseModel):
    api_key: str
    subscription_status: Optional[str] = None


class RevokeSelfPayload(BaseModel):
    api_key: str
    password: str


class RegenerateKeyPayload(BaseModel):
    api_key: str
    password: str


class RevokeByPrefixPayload(BaseModel):
    key_prefix: str
    password: str


class RegenerateByPrefixPayload(BaseModel):
    key_prefix: str
    password: str


class ReconcileTransactionPayload(BaseModel):
    """Admin-only payload to reconcile a Paystack transaction manually."""

    reference: str


class CreateKeyPayload(BaseModel):
    """Payload for creating an API key for a user.

    Contact fields are optional; when omitted, the current user's
    profile (email, first_name, last_name) is used so that contact
    metadata always matches the authenticated user.
    """

    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    tier: str


class FreeKeyPayload(KeyContact):
    """Public free-key creation payload."""


class RegisterPayload(BaseModel):
    """Payload for user registration."""

    first_name: str
    last_name: str
    email: str
    password: str
    confirm_password: str


class VerifyEmailPayload(BaseModel):
    """Payload for verifying a user's email address."""

    email: str
    otp: Optional[str] | None = None
    token: Optional[str] | None = None


class LoginPayload(BaseModel):
    """Payload for user login."""

    email: str
    password: str
