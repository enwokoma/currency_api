"""Main application module for the currency conversion API.

This file wires together all components of the service, including
authentication, rate limiting, billing, and the core currency
conversion endpoints. All requests (except health and free key
creation) require a valid API key. Admin endpoints are secured by an
admin token. Swagger and OpenAPI documentation are also locked
behind API authentication.
"""

from __future__ import annotations

import os
import time
import json
import uuid
from typing import Dict, Any, Optional, Tuple

try:
    # Attempt to import orjson and the high-performance response class.
    import orjson  # type: ignore
    from fastapi.responses import ORJSONResponse
except ImportError:
    # If orjson is not available, fall back to the standard library JSON response.
    import json  # standard library
    from fastapi.responses import JSONResponse  # type: ignore
    # Alias ORJSONResponse to JSONResponse so FastAPI will use it as the default
    ORJSONResponse = JSONResponse  # type: ignore

    class _FakeOrjson:
        """
        Simple shim to emulate a subset of the orjson API used by this
        application. It avoids modifying the global ``json`` module and
        provides ``dumps``/``loads`` compatibly. ``OPT_INDENT_2`` is
        defined only for signature compatibility and is ignored.
        """

        OPT_INDENT_2 = None  # orjson option placeholder

        @staticmethod
        def dumps(obj, option=None):  # type: ignore
            return json.dumps(obj).encode()

        @staticmethod
        def loads(b):  # type: ignore
            if isinstance(b, (bytes, bytearray)):
                b = b.decode()
            return json.loads(b)

    # Provide a fake orjson module so code using orjson.dumps/loads still works
    orjson = _FakeOrjson()  # type: ignore

from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    Depends,
    Query,
    Body,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
# Import JSONResponse and HTMLResponse only. ORJSONResponse is
# conditionally aliased above based on the availability of orjson.
from fastapi.responses import JSONResponse, HTMLResponse  # noqa: F401
from fastapi.openapi.utils import get_openapi
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html

from pydantic import BaseModel

from auth import (
    validate_api_key,
    generate_api_key,
    generate_free_key,
    revoke_api_key,
    rotate_api_key,
    restore_api_key,
    load_keys,
    save_keys,
    upgrade_key_tier_by_hash,
    charge_call_for_key,
    SECONDS_PER_MONTH,
    update_key_contact,
    mark_pending_plan,
    find_key_by_email,
    hash_key,
)
from rate_limit import allow_request
from utils import validate_currency, fetch_rates, fetch_historical_rates
from billing import (
    initialize_transaction,
    verify_paystack_signature,
    extract_webhook_info,
    disable_subscription,
)
from transactions import (
    record_transaction,
    update_transactions_for_key,
    load_transactions,
    get_transactions_for_key,
    get_pending_transaction_for_key,
    get_latest_transaction_for_key,
)
from usage_stats import record_usage_event, get_usage_map

# Import user management helpers. We avoid circular imports by placing this
# import at module level rather than within endpoint functions. The users
# module provides synchronous helper functions for creating users,
# verifying email addresses, authenticating credentials, managing
# session tokens and retrieving user records. See users.py for details.
from users import (
    create_user,
    authenticate_user,
    verify_user,
    start_verification,
    create_session,
    revoke_session,
    get_user_by_session,
    sanitize_user,
)


from webhook_logs import log_webhook_event

# Try to load environment variables from a .env file if python-dotenv is installed.
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except ImportError:
    # python-dotenv is optional; environment variables can still be provided
    pass



# The admin token for key management. This is loaded from the
# environment if present; otherwise a secure token will be generated
# and persisted on disk. See `_load_or_create_admin_token` below.
ADMIN_FILE = "admin_token.txt"


def _load_or_create_admin_token() -> str:
    # If a token is set via environment, use it
    env_token = os.getenv("ADMIN_TOKEN")
    if env_token:
        return env_token
    # If token file exists, read it
    if os.path.exists(ADMIN_FILE):
        with open(ADMIN_FILE, "r", encoding="utf-8") as fh:
            token = fh.read().strip()
            if token:
                return token
    # Otherwise generate a new secure token and save it
    import secrets

    token = "admin_token_" + secrets.token_hex(32)
    with open(ADMIN_FILE, "w", encoding="utf-8") as fh:
        fh.write(token)
    print(
        "\n=============================================\n"
        "  NEW ADMIN TOKEN GENERATED\n"
        f" Your Admin Token: {token}\n"
        " (Saved to admin_token.txt)\n"
        "=============================================\n"
    )
    return token


ADMIN_TOKEN = _load_or_create_admin_token()


# Subscription plans. Update this list to match the plans created in
# your Paystack dashboard. Each plan defines the price in Naira, the
# rate limits (per minute and per day), and the monthly credits
# (billable calls) available on the plan. ``monthly_credits`` can be
# None to indicate unlimited billable calls.
# Subscription plans. Update this list to match the plans created in
# your Paystack dashboard. Each plan defines the price in Naira, the
# rate limits (per minute and per day), monthly credits, and the
# corresponding Paystack plan code. The Paystack plan codes are
# loaded from environment variables so that they are not hardcoded in
# the source. If the environment variable is missing for a plan, the
# plan will not include a ``paystack_plan_code`` and attempting to
# upgrade to it will fail with a clear error.

# Load Paystack plan codes from environment. These environment
# variables should be defined in your .env file or the deployment
# environment. They map our internal plan codes to the codes defined
# on your Paystack dashboard.
_PAYSTACK_PLAN_BASIC = os.getenv("PAYSTACK_PLAN_BASIC")
_PAYSTACK_PLAN_PRO = os.getenv("PAYSTACK_PLAN_PRO")

PLANS = [
    {
        "code": "BASIC_MONTHLY",
        "name": "Basic Monthly",
        "price_naira": 2000,
        "currency": "NGN",
        "requests_per_minute": 100,
        "requests_per_day": 10_000,
        "monthly_credits": 5000,
        # Include the Paystack plan code for this plan. Use the
        # environment variable PAYSTACK_PLAN_BASIC to avoid
        # hardcoding sensitive values in the repository.
        "paystack_plan_code": _PAYSTACK_PLAN_BASIC,
    },
    {
        "code": "PRO_MONTHLY",
        "name": "Pro Monthly",
        "price_naira": 5000,
        "currency": "NGN",
        "requests_per_minute": 300,
        "requests_per_day": 50_000,
        "monthly_credits": 20_000,
        # Include the Paystack plan code for this plan from
        # environment variable PAYSTACK_PLAN_PRO.
        "paystack_plan_code": _PAYSTACK_PLAN_PRO,
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


# ---------------------------------------------------------------------------
# Helpers for timestamp conversion
# ---------------------------------------------------------------------------
# Define West Africa Time (UTC+1) timezone for converting numeric timestamps.
from datetime import datetime, timezone, timedelta

# West Africa Time (Lagos) does not observe daylight savings.
WAT = timezone(timedelta(hours=1))

def to_wat_iso(value: Any) -> Any:
    """
    Convert a numeric timestamp (epoch) to a West Africa Time ISO string without timezone.

    If ``value`` is a float or int representing seconds since the UNIX epoch,
    it will be converted to a ``datetime`` in the WAT timezone, then the
    timezone information will be removed before converting to ISO 8601 format
    with millisecond precision. This omits the ``+01:00`` suffix for a
    cleaner display while still representing Africa/Lagos local time.

    Non-numeric values are returned unchanged.
    """
    if isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value, WAT)
        naive = dt.replace(tzinfo=None)
        return naive.isoformat(timespec="milliseconds")
    return value


# Pydantic models for request bodies
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


class UpgradePayload(BaseModel):
    plan_code: str
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None


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


class SubscribePayload(KeyContact):
    """Direct paid subscription payload (without requiring an existing key)."""

    plan_code: str
    owner: Optional[str] = None  # Optional identifier (e.g., user id) to tag the key


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

# ---------------------------------------------------------------------------
# User management models
# ---------------------------------------------------------------------------

class RegisterPayload(BaseModel):
    """Payload for user registration.

    Clients must supply first and last names, an email address and
    password fields. The ``password`` and ``confirm_password`` fields
    must match. Passwords must be at least 8 characters long; this is
    enforced in ``users.create_user``.
    """

    first_name: str
    last_name: str
    email: str
    password: str
    confirm_password: str


class VerifyEmailPayload(BaseModel):
    """Payload for verifying a user's email address.

    Either ``otp`` or ``token`` must be provided. The email field
    identifies the user to verify. Verification codes expire after 15
    minutes. To request a new code, clients can call the
    ``/auth/resend-verification`` endpoint (implemented below).
    """

    email: str
    otp: Optional[str] | None = None
    token: Optional[str] | None = None


class LoginPayload(BaseModel):
    """Payload for user login.

    The email/password combination must correspond to a registered
    user whose email has been verified. On success, a short-lived
    session token is returned.
    """

    email: str
    password: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize_contact_fields(email: str, first_name: str, last_name: str) -> tuple[str, str, str]:
    """Normalize and validate contact details."""
    email_norm = email.strip().lower()
    first = first_name.strip()
    last = last_name.strip()
    if not email_norm:
        raise HTTPException(status_code=400, detail="Email is required")
    if not first or not last:
        raise HTTPException(status_code=400, detail="First name and last name are required")
    return email_norm, first, last


def _get_plan_or_error(plan_code: str) -> Dict[str, Any]:
    """Return a plan definition or raise HTTP 400."""
    plan = next((p for p in PLANS if p["code"] == plan_code), None)
    if not plan:
        raise HTTPException(status_code=400, detail="Unknown plan_code")
    paystack_code = plan.get("paystack_plan_code")
    if not paystack_code:
        raise HTTPException(
            status_code=400,
            detail=f"Paystack plan code is not configured for plan '{plan['code']}'",
        )
    return plan


def _rate_limits_for_record(record: Dict[str, Any]) -> Tuple[int | None, int | None]:
    """Determine rate limits for a key record."""
    tier = record.get("tier", "paid")
    plan_code = record.get("plan_code")
    if tier == "free":
        return FREE_REQUESTS_PER_MINUTE, FREE_REQUESTS_PER_DAY
    if plan_code:
        plan = next((p for p in PLANS if p["code"] == plan_code), None)
        if plan:
            return (
                plan.get("requests_per_minute", DEFAULT_PAID_REQUESTS_PER_MINUTE),
                plan.get("requests_per_day", DEFAULT_PAID_REQUESTS_PER_DAY),
            )
    return DEFAULT_PAID_REQUESTS_PER_MINUTE, DEFAULT_PAID_REQUESTS_PER_DAY


def _compose_usage_entry(
    record: Dict[str, Any],
    tracked: Dict[str, Any],
    per_minute: int | None,
    per_day: int | None,
) -> Dict[str, Any]:
    """Combine key metadata with tracked usage counters."""
    return {
        "owner": record.get("owner"),
        "email": record.get("email"),
        "first_name": record.get("first_name"),
        "last_name": record.get("last_name"),
        "active": record.get("active", True),
        "revoked": bool(record.get("revoked_at")),
        "tier": record.get("tier", "paid"),
        "plan_code": record.get("plan_code"),
        "pending_plan_code": record.get("pending_plan_code"),
        "subscription_status": record.get("subscription_status"),
        "remaining_credits": record.get("credits"),
        "credits_reset_at": record.get("credits_reset_at"),
        "credits_reset_at_wat": to_wat_iso(record.get("credits_reset_at")),
        "rate_limits": {
            "per_minute": per_minute,
            "per_day": per_day,
        },
        "key_prefix": (record.get("key_hash") or "")[:8] + ".",
        "created_at": to_wat_iso(record.get("created_at")),
        "upgraded_at": to_wat_iso(record.get("upgraded_at")),
        "revoked_at": to_wat_iso(record.get("revoked_at")),
        "total_requests": tracked.get("total_requests", 0),
        "billable_requests": tracked.get("billable_requests", 0),
        "credits_used": tracked.get("credits_used", 0),
        "first_request_at": to_wat_iso(tracked.get("first_request_at")),
        "last_request_at": to_wat_iso(tracked.get("last_request_at")),
    }


def _get_user_key_record_or_error(raw_key: str, user_id: str) -> Dict[str, Any]:
    """Fetch a key by raw value and ensure it belongs to the user."""
    hashed = hash_key(raw_key)
    data = load_keys()
    for record in data.get("keys", []):
        if record.get("key_hash") == hashed and record.get("owner") == user_id:
            return record
    raise HTTPException(status_code=404, detail="API key not found for this user")


def _get_user_key_by_prefix(user_id: str, key_prefix: str) -> Dict[str, Any]:
    """Find a key owned by user via key hash prefix."""
    data = load_keys()
    for record in data.get("keys", []):
        key_hash = record.get("key_hash", "")
        if record.get("owner") == user_id and key_hash.startswith(key_prefix):
            return record
    raise HTTPException(status_code=404, detail="API key not found for this user")


def _get_key_by_prefix(prefix: str) -> Dict[str, Any]:
    """Find a key globally via key hash prefix (requires uniqueness)."""
    data = load_keys()
    matches = [r for r in data.get("keys", []) if (r.get("key_hash") or "").startswith(prefix)]
    if not matches:
        raise HTTPException(status_code=401, detail="Invalid API key prefix")
    if len(matches) > 1:
        raise HTTPException(status_code=400, detail="API key prefix is not unique; provide full key")
    rec = dict(matches[0])
    rec["key_hash"] = matches[0].get("key_hash")
    return rec


def _revoke_key_record(record: Dict[str, Any]) -> None:
    data = load_keys()
    for rec in data.get("keys", []):
        if rec.get("key_hash") == record.get("key_hash"):
            rec["active"] = False
            rec["subscription_status"] = "revoked"
            rec["pending_plan_code"] = None
            rec["revoked_at"] = time.time()
            save_keys(data)
            return
    raise HTTPException(status_code=404, detail="API key not found")


def _rotate_key_record(record: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Rotate key without knowing the raw key."""
    data = load_keys()
    for rec in data.get("keys", []):
        if rec.get("key_hash") != record.get("key_hash"):
            continue
        if rec.get("tier") == "free":
            new_raw = "free_" + uuid.uuid4().hex[:32]
        else:
            new_raw = str(uuid.uuid4())
        rec["key_hash"] = hash_key(new_raw)
        save_keys(data)
        updated = dict(rec)
        updated["key_hash"] = rec["key_hash"]
        return new_raw, updated
    raise HTTPException(status_code=404, detail="API key not found")


def _start_subscription_checkout(
    *,
    key_hash: str,
    plan: Dict[str, Any],
    email: str,
    first_name: str,
    last_name: str,
    deactivate: bool = False,
) -> Tuple[str, str]:
    """Mark a key as pending a plan and start Paystack checkout."""
    paystack_code = plan.get("paystack_plan_code")
    if not paystack_code:
        raise HTTPException(
            status_code=400,
            detail=f"Paystack plan code is not configured for plan '{plan['code']}'",
        )
    # Flag the key as pending upgrade (optionally deactivate until payment completes)
    mark_pending_plan(key_hash, plan["code"], deactivate=deactivate)
    auth_url, reference = initialize_transaction(
        email=email,
        amount_naira=plan["price_naira"],
        api_key_hash=key_hash,
        paystack_plan_code=paystack_code,
        metadata_plan_code=plan["code"],
        customer={"first_name": first_name, "last_name": last_name},
        extra_metadata={"email": email},
    )
    # Record this transaction as pending in the transaction history.
    record_transaction(
        api_key_hash=key_hash,
        email=email,
        plan_code=plan["code"],
        paystack_plan_code=paystack_code,
        amount_naira=plan["price_naira"],
        reference=reference,
        status="pending",
        authorization_url=auth_url,
    )
    return auth_url, reference


# FastAPI app configuration
class PrettyJSONResponse(JSONResponse):
    """Render JSON with indentation for readability."""

    media_type = "application/json"

    def render(self, content: Any) -> bytes:
        try:
            return orjson.dumps(content, option=orjson.OPT_INDENT_2)
        except Exception:
            return json.dumps(content, ensure_ascii=False, indent=2).encode("utf-8")


# FastAPI app configuration
app = FastAPI(
    title="Currency Conversion API",
    description=(
        "Fast, cached currency conversion API using Fawaz Ahmed's Exchange API "
        "with fallback provider, API keys, rate limiting, free and paid tiers, "
        "and Paystack subscription upgrades."
    ),
    version="3.1.0",
    default_response_class=PrettyJSONResponse,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# Enable CORS for all origins (adjust in production as needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Middleware: logging requests and pretty JSON formatting
@app.middleware("http")
async def request_logger(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = round((time.time() - start) * 1000, 2)
    print(
        f"[{request.method}] {request.url.path} {response.status_code} {duration}ms"
    )
    return response


@app.exception_handler(HTTPException)
async def pretty_http_exception(request: Request, exc: HTTPException):
    """Ensure HTTP errors are returned as pretty JSON."""
    return PrettyJSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.middleware("http")
async def prettify_json(request: Request, call_next):
    resp = await call_next(request)
    return resp


# Dependency: enforce API key presence and rate limiting
def api_key_required(request: Request) -> Dict[str, Any]:
    """Resolve the calling key, enforce rate limits and charge credits.

    For free tier keys, uses ``FREE_REQUESTS_PER_MINUTE``,
    ``FREE_REQUESTS_PER_DAY`` and ``FREE_TIER_MONTHLY_CREDITS``.
    For paid keys, derives limits and credits from the plan. If no
    plan is bound, defaults to the Basic limits (100/min, 10k/day) and
    unlimited credits.
    """
    raw_key = request.headers.get("X-API-KEY") or request.query_params.get("api_key")
    key_record = validate_api_key(raw_key)
    tier = key_record.get("tier", "paid")
    per_minute, per_day = _rate_limits_for_record(key_record)
    # Enforce rate limits
    allow_request(
        key_hash=key_record["key_hash"],
        per_minute=per_minute,
        per_day=per_day,
    )
    # Charge credits
    updated_record, billed = charge_call_for_key(
        key_record=key_record,
        free_monthly_credits=FREE_TIER_MONTHLY_CREDITS,
        plans=PLANS,
    )
    record_usage_event(
        key_hash=updated_record.get("key_hash"),
        owner=updated_record.get("owner"),
        email=updated_record.get("email"),
        first_name=updated_record.get("first_name"),
        last_name=updated_record.get("last_name"),
        tier=updated_record.get("tier", tier),
        plan_code=updated_record.get("plan_code"),
        subscription_status=updated_record.get("subscription_status"),
        billed=billed,
        remaining_credits=updated_record.get("credits"),
        credits_reset_at=updated_record.get("credits_reset_at"),
    )
    # Keep caller-visible record in sync with the latest persisted data
    key_record.update(
        {
            "plan_code": updated_record.get("plan_code"),
            "pending_plan_code": updated_record.get("pending_plan_code"),
            "subscription_status": updated_record.get("subscription_status"),
            "credits": updated_record.get("credits"),
            "credits_reset_at": updated_record.get("credits_reset_at"),
        }
    )
    return key_record


def paid_key_required(request: Request) -> Dict[str, Any]:
    """Enforce that the caller is using a paid key (for premium endpoints)."""
    key_record = api_key_required(request)
    if key_record.get("tier") != "paid":
        raise HTTPException(
            status_code=403,
            detail="This endpoint is available to paid subscriptions only.",
        )
    return key_record


def billing_key_required(request: Request) -> Dict[str, Any]:
    """Allow pending/inactive keys to initiate billing flows."""
    raw_key = request.headers.get("X-API-KEY") or request.query_params.get("api_key")
    prefix = request.headers.get("X-API-KEY-PREFIX") or request.query_params.get("api_key_prefix")
    if raw_key:
        key_record = validate_api_key(raw_key, allow_inactive=True, allow_unpaid=True)
    elif prefix:
        key_record = _get_key_by_prefix(prefix)
        # Apply basic validation similar to validate_api_key for billing flows
        if not key_record.get("active", True) and key_record.get("subscription_status") not in {"pending", None}:
            raise HTTPException(
                status_code=403,
                detail="This key is inactive. Contact support or generate a new key.",
            )
        key_record.setdefault("tier", "paid")
        key_record.setdefault("subscription_status", "active" if key_record.get("tier") == "paid" else "free")
    else:
        raise HTTPException(status_code=401, detail="Missing API Key")
    # Hard-block revoked or inactive keys that are not merely pending activation
    if (
        not key_record.get("active", True)
        and key_record.get("subscription_status") not in {"pending", None}
    ) or key_record.get("revoked_at"):
        raise HTTPException(
            status_code=403,
            detail="This key is inactive. Contact support or generate a new key.",
        )
    return key_record


# ---------------------------------------------------------------------------
# Session authentication dependency
# ---------------------------------------------------------------------------

def session_required(request: Request) -> Dict[str, Any]:
    """Resolve the calling user based on a session token.

    Session tokens are short-lived values created by the ``/auth/login``
    endpoint. They must be supplied in the ``X-SESSION-TOKEN`` header or
    as a ``session_token`` query parameter. The helper function
    ``get_user_by_session`` performs all validation, including expiry
    checks and email verification. If the token is invalid, expired or
    revoked, an ``HTTPException`` is raised.

    Returns:
        The full user record associated with the session token. Callers
        should pass this record through ``sanitize_user`` before
        returning it to clients.
    """
    token = request.headers.get("X-SESSION-TOKEN") or request.query_params.get(
        "session_token"
    )
    user = get_user_by_session(token)
    return user


# ---------------------------------------------------------------------------
# Utility and admin authentication dependencies
# ---------------------------------------------------------------------------

def admin_required(request: Request) -> bool:
    """Validate the admin token for protected admin endpoints.

    The expected admin token is determined dynamically. If an
    ``ADMIN_TOKEN`` environment variable is set, it takes priority.
    Otherwise, the module-level ``ADMIN_TOKEN`` value (generated or
    loaded from ``admin_token.txt``) is used. This allows tests to
    override the admin token via environment variables even after the
    app has been imported.
    """
    provided = request.headers.get("X-ADMIN-TOKEN") or request.query_params.get(
        "admin_token"
    )
    # Determine the expected token: prefer environment variable, else fallback
    expected = os.getenv("ADMIN_TOKEN") or ADMIN_TOKEN
    if provided != expected:
        raise HTTPException(status_code=403, detail="Invalid admin token")
    return True

# ---------------------------------------------------------------------------
# Usage information endpoints
# ---------------------------------------------------------------------------

@app.get("/me/usage", tags=["Usage"])  # new endpoint
def get_usage(key: Dict[str, Any] = Depends(api_key_required)) -> Dict[str, Any]:
    """Return remaining credits and rate limits for the caller's API key.

    This endpoint does not consume credits. It resets credits if the
    current period has expired.
    """
    # Determine tier and plan
    tier = key.get("tier", "paid")
    plan_code = key.get("plan_code")
    # Compute rate limits consistent with api_key_required
    if tier == "free":
        per_minute = FREE_REQUESTS_PER_MINUTE
        per_day = FREE_REQUESTS_PER_DAY
        plan_info: Optional[Dict[str, Any]] = None
    else:
        if plan_code:
            plan_info = next((p for p in PLANS if p["code"] == plan_code), None)
            if plan_info:
                per_minute = plan_info.get("requests_per_minute", DEFAULT_PAID_REQUESTS_PER_MINUTE)
                per_day = plan_info.get("requests_per_day", DEFAULT_PAID_REQUESTS_PER_DAY)
            else:
                plan_info = None
                per_minute = DEFAULT_PAID_REQUESTS_PER_MINUTE
                per_day = DEFAULT_PAID_REQUESTS_PER_DAY
        else:
            plan_info = None
            per_minute = DEFAULT_PAID_REQUESTS_PER_MINUTE
            per_day = DEFAULT_PAID_REQUESTS_PER_DAY
    # Reset credits if needed and compute remaining credits
    now = time.time()
    # Load keys to update if necessary
    # Import from the local auth module to avoid circular references
    from auth import load_keys, save_keys
    data = load_keys()
    credits = None
    next_reset = None
    for record in data.get("keys", []):
        if record.get("key_hash") == key["key_hash"]:
            # Reset if expired
            reset_at = record.get("credits_reset_at")
            if reset_at is not None and now >= reset_at:
                # Determine plan again
                plan = None
                if plan_code:
                    plan = next((p for p in PLANS if p["code"] == plan_code), None)
                if tier == "free":
                    record["credits"] = FREE_TIER_MONTHLY_CREDITS
                    record["credits_reset_at"] = now + SECONDS_PER_MONTH
                else:
                    if plan:
                        mc = plan.get("monthly_credits")
                        record["credits"] = mc
                        if mc is None:
                            record["credits_reset_at"] = None
                        else:
                            record["credits_reset_at"] = now + SECONDS_PER_MONTH
                    else:
                        record["credits"] = None
                        record["credits_reset_at"] = None
                save_keys(data)
            credits = record.get("credits")
            next_reset = record.get("credits_reset_at")
            break
    return {
        "tier": tier,
        "plan_code": plan_code,
        "rate_limits": {"per_minute": per_minute, "per_day": per_day},
        "remaining_credits": credits,
        "credits_reset_at": next_reset,
        "revoked": bool(key.get("revoked_at")),
        "revoked_at": to_wat_iso(key.get("revoked_at")),
    }


@app.get("/admin/usage", tags=["Admin"])  # new admin endpoint
def admin_usage(_: bool = Depends(admin_required)) -> Dict[str, Any]:
    """Return usage statistics for all API keys.

    This endpoint provides an overview of every key's remaining credits,
    reset timestamp and current rate limits. It also performs credit
    resets when the ``credits_reset_at`` timestamp has passed. Using
    floats for timestamps ensures correct comparisons against the
    current time.
    """
    # Import here to avoid circular references. Use relative import because
    # this module is part of the same package. Without the leading dot,
    # FastAPI may import the wrong module or raise ModuleNotFoundError in
    # certain contexts (e.g., tests running from outside the package).
    from auth import load_keys, save_keys

    data = load_keys()
    usage_map = get_usage_map()
    now_ts = time.time()  # current epoch time
    usage: list[Dict[str, Any]] = []
    modified = False
    for record in data.get("keys", []):
        tier = record.get("tier", "paid")
        plan_code = record.get("plan_code")
        per_minute, per_day = _rate_limits_for_record(record)

        # ----------------------------
        # Reset credits if the reset period has expired
        # ----------------------------
        reset_at = record.get("credits_reset_at")
        if reset_at is not None and now_ts >= reset_at:
            if tier == "free":
                # Free keys get a fresh bucket of free credits
                record["credits"] = FREE_TIER_MONTHLY_CREDITS
                record["credits_reset_at"] = now_ts + SECONDS_PER_MONTH
            else:
                # Paid keys: if plan has monthly_credits, reset; otherwise unlimited
                if plan_code:
                    plan = next((p for p in PLANS if p["code"] == plan_code), None)
                else:
                    plan = None
                if plan:
                    mc = plan.get("monthly_credits")
                    record["credits"] = mc
                    # Unlimited credits -> no next reset
                    if mc is None:
                        record["credits_reset_at"] = None
                    else:
                        record["credits_reset_at"] = now_ts + SECONDS_PER_MONTH
                else:
                    # Paid, no plan -> unlimited credits, no reset
                    record["credits"] = None
                    record["credits_reset_at"] = None
            modified = True

        # ----------------------------
        # Append usage entry for this key
        # ----------------------------
        tracked = usage_map.get(record.get("key_hash"), {}) or {}
        usage.append(_compose_usage_entry(record, tracked, per_minute, per_day))

    # Persist any modifications (credit resets)
    if modified:
        save_keys(data)

    return {"usage": usage}


    # Note: ``admin_required`` is now defined above the usage endpoints to
    # ensure it exists when referenced in dependency definitions.


@app.get("/health", tags=["Utility"])
def health() -> Dict[str, str]:
    """Health check endpoint (no authentication required)."""
    return {"status": "ok"}


@app.post("/rates", tags=["Core"], response_model=Dict[str, Any])
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


@app.post("/rate", tags=["Core"], response_model=Dict[str, Any])
def get_rate(
    payload: RateRequest,
    _: Dict[str, Any] = Depends(api_key_required),
) -> Dict[str, Any]:
    """Retrieve the conversion rate from one currency to another."""
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


@app.post("/convert", tags=["Core"], response_model=Dict[str, Any])
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


@app.post("/rates/historical", tags=["Core"], response_model=Dict[str, Any])
def get_historical_rates_endpoint(
    payload: HistoricalBaseRatesRequest,
    _: Dict[str, Any] = Depends(paid_key_required),
) -> Dict[str, Any]:
    """Retrieve all rates for a base currency on a specific date (paid only)."""
    base_lower = validate_currency(payload.base_currency)
    rates = fetch_historical_rates(base_lower, payload.date)
    return {
        "base": payload.base_currency.upper(),
        "date": payload.date,
        "rates": rates,
    }


@app.post("/rate/historical", tags=["Core"], response_model=Dict[str, Any])
def get_historical_rate(
    payload: HistoricalRateRequest,
    _: Dict[str, Any] = Depends(paid_key_required),
) -> Dict[str, Any]:
    """Retrieve a historical conversion rate between two currencies (paid only)."""
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


@app.post("/convert/historical", tags=["Core"], response_model=Dict[str, Any])
def convert_currency_historical(
    payload: HistoricalConvertRequest,
    _: Dict[str, Any] = Depends(paid_key_required),
) -> Dict[str, Any]:
    """Convert an amount between two currencies on a specific date (paid only)."""
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


@app.post("/auth/free-key", tags=["Auth"])
def get_free_key(payload: FreeKeyPayload) -> Dict[str, str]:
    """Public endpoint to request a free-tier API key."""
    raw = generate_free_key(
        owner=payload.email.strip().lower(),
        email=payload.email,
        first_name=payload.first_name,
        last_name=payload.last_name,
    )
    return {
        "tier": "free",
        "api_key": raw,
        "note": "Free keys have limited quota. Upgrade for higher limits.",
    }


@app.get("/plans", tags=["Billing"])
def list_plans() -> Dict[str, Any]:
    """Public endpoint to list available subscription plans."""
    return {"plans": PLANS}


@app.post("/billing/subscribe", tags=["Billing"], response_model=Dict[str, Any])
def billing_subscribe(payload: SubscribePayload) -> Dict[str, Any]:
    """Create a paid key and immediately start Paystack checkout (public)."""
    plan = _get_plan_or_error(payload.plan_code)
    email, first, last = _normalize_contact_fields(
        payload.email, payload.first_name, payload.last_name
    )
    # Ensure the email is not already tied to another key
    existing = find_key_by_email(email, include_inactive=True, include_revoked=True)
    if existing:
        raise HTTPException(
            status_code=400,
            detail="Email already has an API key. Use /billing/upgrade with that key.",
        )
    owner = payload.owner or email
    raw_key = generate_api_key(
        owner=owner,
        email=email,
        first_name=first,
        last_name=last,
        active=False,
        subscription_status="pending",
        pending_plan_code=plan["code"],
    )
    record = find_key_by_email(email)
    key_hash = record.get("key_hash") if record else None
    if not key_hash:
        raise HTTPException(status_code=500, detail="Failed to persist generated key")
    auth_url, reference = _start_subscription_checkout(
        key_hash=key_hash,
        plan=plan,
        email=email,
        first_name=first,
        last_name=last,
        deactivate=True,
    )
    return {
        "tier": "paid",
        "api_key": raw_key,
        "plan": plan,
        "authorization_url": auth_url,
        "reference": reference,
        "message": "Complete payment to activate this key. It remains inactive until Paystack confirms.",
    }


@app.post("/billing/upgrade", tags=["Billing"], response_model=Dict[str, Any])
def billing_upgrade(
    payload: UpgradePayload,
    key: Dict[str, Any] = Depends(billing_key_required),
) -> Dict[str, Any]:
    """Start a Paystack transaction to upgrade a free key to a paid plan.

    The caller must supply a ``plan_code`` that exists in ``PLANS``.
    Contact information is taken from the payload if provided, else
    from the key record. The API key used for authentication will
    determine which key is upgraded.
    """
    plan = _get_plan_or_error(payload.plan_code)
    email = (payload.email or key.get("email") or "").strip().lower()
    first = payload.first_name or key.get("first_name")
    last = payload.last_name or key.get("last_name")
    if not email or not first or not last:
        raise HTTPException(
            status_code=400,
            detail="Email, first_name and last_name are required to upgrade.",
        )
    # Block duplicate/pending upgrades
    if key.get("pending_plan_code"):
        pending_txn = get_pending_transaction_for_key(key["key_hash"])
        if pending_txn and pending_txn.get("authorization_url"):
            return {
                "authorization_url": pending_txn.get("authorization_url"),
                "reference": pending_txn.get("reference"),
                "plan": plan,
                "message": "Continue the existing pending payment to activate your plan.",
            }
        last_txn = get_latest_transaction_for_key(key["key_hash"])
        if last_txn and last_txn.get("authorization_url"):
            return {
                "authorization_url": last_txn.get("authorization_url"),
                "reference": last_txn.get("reference"),
                "plan": plan,
                "message": "Continue the existing pending payment to activate your plan.",
            }
        # No transaction found; re-start checkout using the pending plan
        auth_url, reference = _start_subscription_checkout(
            key_hash=key["key_hash"],
            plan=plan,
            email=email,
            first_name=first,
            last_name=last,
            deactivate=False,
        )
        return {
            "authorization_url": auth_url,
            "reference": reference,
            "plan": plan,
            "message": "Continue the existing pending payment to activate your plan.",
        }
    # Block re-purchasing the same active plan unless credits are exhausted
    if key.get("plan_code") == plan["code"] and key.get("subscription_status") == "active":
        credits = key.get("credits")
        if credits is None or credits > 0:
            raise HTTPException(
                status_code=400,
                detail="Key already has this active plan. Choose a higher plan or wait for renewal.",
            )
    # Ensure the key carries the latest contact info
    update_key_contact(key["key_hash"], email=email, first_name=first, last_name=last)
    auth_url, reference = _start_subscription_checkout(
        key_hash=key["key_hash"],
        plan=plan,
        email=email,
        first_name=first,
        last_name=last,
        deactivate=False,
    )
    return {
        "authorization_url": auth_url,
        "reference": reference,
        "plan": plan,
        "message": "Complete the payment to activate your paid plan.",
    }


@app.post("/billing/cancel", tags=["Billing"], response_model=Dict[str, Any])
def cancel_subscription(
    key: Dict[str, Any] = Depends(paid_key_required),
) -> Dict[str, Any]:
    """Cancel/deactivate the current subscription for the calling key."""
    sub_code = key.get("paystack_subscription_code")
    email_token = key.get("paystack_email_token")
    if not sub_code or not email_token:
        raise HTTPException(
            status_code=400,
            detail="No active Paystack subscription is associated with this key.",
        )
    disable_subscription(sub_code, email_token)
    upgrade_key_tier_by_hash(
        key_hash=key["key_hash"],
        new_tier="free",
        plan=None,
        free_monthly_credits=FREE_TIER_MONTHLY_CREDITS,
        subscription_status="canceled",
        active=True,
    )
    update_transactions_for_key(key["key_hash"], new_status="cancelled")
    return {"status": "cancelled", "message": "Subscription cancelled and key downgraded to free."}


@app.post("/paystack/webhook", status_code=status.HTTP_200_OK, tags=["Billing"])
async def paystack_webhook(request: Request) -> Dict[str, str]:
    """Handle Paystack webhook events for subscription management."""
    body = await request.body()
    log_webhook_event(body)  # Log the raw webhook body
    signature = request.headers.get("x-paystack-signature")
    if not verify_paystack_signature(body, signature):
        raise HTTPException(status_code=400, detail="Invalid Paystack signature")
    event_type, api_key_hash, plan_code, info = extract_webhook_info(body)
    subscription_code = info.get("subscription_code")
    email_token = info.get("email_token")
    # Successful payment or subscription activation
    if event_type in ("charge.success", "subscription.create", "subscription.enable"):
        if api_key_hash and plan_code:
            plan = next((p for p in PLANS if p["code"] == plan_code), None)
            if plan:
                record = next((k for k in load_keys().get("keys", []) if k.get("key_hash") == api_key_hash), None)
                if record and record.get("revoked_at"):
                    update_transactions_for_key(api_key_hash, new_status="failed")
                else:
                    upgrade_key_tier_by_hash(
                        key_hash=api_key_hash,
                        new_tier="paid",
                        plan=plan,
                        free_monthly_credits=FREE_TIER_MONTHLY_CREDITS,
                        paystack_subscription_code=subscription_code,
                        paystack_email_token=email_token,
                        subscription_status="active",
                        active=True,
                    )
                    update_transactions_for_key(api_key_hash, new_status="success")
    if event_type in ("subscription.disable", "invoice.failed", "invoice.create_failed"):
        if api_key_hash:
            status = "cancelled" if event_type == "subscription.disable" else "failed"
            upgrade_key_tier_by_hash(
                key_hash=api_key_hash,
                new_tier="free",
                plan=None,
                free_monthly_credits=FREE_TIER_MONTHLY_CREDITS,
                subscription_status="canceled",
                active=True,
            )
            update_transactions_for_key(api_key_hash, new_status=status)
    return {"status": "ok"}



@app.post("/admin/generate-key", tags=["Admin"])
def admin_generate_key(
    payload: OwnerPayload,
    _: bool = Depends(admin_required),
) -> Dict[str, Any]:
    """Admin endpoint to generate a new paid API key."""
    plan = _get_plan_or_error(payload.plan_code) if payload.plan_code else None
    email, first, last = _normalize_contact_fields(
        payload.email, payload.first_name, payload.last_name
    )
    raw = generate_api_key(
        payload.owner,
        email=email,
        first_name=first,
        last_name=last,
        plan_code=plan["code"] if plan else None,
        active=payload.activate,
        subscription_status="active" if payload.activate else "pending",
    )
    record = find_key_by_email(email)
    key_hash = record.get("key_hash") if record else None
    if plan and key_hash:
        upgrade_key_tier_by_hash(
            key_hash=key_hash,
            new_tier="paid",
            plan=plan,
            free_monthly_credits=FREE_TIER_MONTHLY_CREDITS,
            subscription_status="active" if payload.activate else "pending",
            active=payload.activate,
        )
    return {
        "owner": payload.owner,
        "api_key": raw,
        "plan_code": plan["code"] if plan else None,
        "contact": {"email": email, "first_name": first, "last_name": last},
        "active": payload.activate,
    }


@app.post("/admin/revoke-key", tags=["Admin"])
def admin_revoke_key(
    payload: RevokePayload,
    _: bool = Depends(admin_required),
) -> Dict[str, str]:
    """Admin endpoint to revoke an existing API key."""
    revoke_api_key(payload.api_key)
    return {"status": "ok", "message": "API key revoked"}


@app.post("/admin/unrevoke-key", tags=["Admin"])
def admin_unrevoke_key(
    payload: RestorePayload,
    _: bool = Depends(admin_required),
) -> Dict[str, Any]:
    """Admin endpoint to reactivate a previously revoked/inactive API key."""
    record = restore_api_key(
        payload.api_key, subscription_status=payload.subscription_status
    )
    return {
        "status": "ok",
        "message": "API key reactivated",
        "active": record.get("active", True),
        "subscription_status": record.get("subscription_status"),
        "tier": record.get("tier"),
    }


# ---------------------------------------------------------------------------
# User registration, verification and session management endpoints
# ---------------------------------------------------------------------------

@app.post("/auth/register", tags=["User"], status_code=status.HTTP_201_CREATED)
def register_user(payload: RegisterPayload) -> Dict[str, Any]:
    """Register a new user.

    This endpoint accepts a first name, last name, email address and
    password. The password must be at least 8 characters long and
    match the ``confirm_password`` field. On successful registration,
    the user's email remains unverified until the verification step is
    completed. A verification code (OTP) and a verification token are
    returned in the response for demonstration purposes; in a real
    deployment these values would be sent via email.

    Raises:
        HTTPException: If the email is already registered, the
        password is too short or passwords do not match.
    """
    if payload.password != payload.confirm_password:
        raise HTTPException(
            status_code=400, detail="Password and confirm password do not match"
        )
    # Create the user. The helper will normalise the email, hash the
    # password and generate an OTP and verification token. It will
    # raise HTTPException if the email is already registered or the
    # password is too short.
    user_rec = create_user(
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=payload.email,
        password=payload.password,
    )
    # Extract verification info for demonstration/testing. In a real
    # system you would send the OTP or token via email and not return
    # it in the API response.
    verification = user_rec.get("verification", {}) or {}
    return {
        "message": "User registered successfully. Please verify your email.",
        "user": sanitize_user(user_rec),
        "verification": {
            "otp": verification.get("otp"),
            "token": verification.get("token"),
            "expires_at": verification.get("expires_at"),
        },
    }


@app.post("/auth/resend-verification", tags=["User"])
def resend_verification(email: str = Body(..., embed=True)) -> Dict[str, Any]:
    """Resend the verification code/link for an unverified user.

    This endpoint regenerates a verification OTP and token for the
    specified email address. If the user is already verified, an
    error is raised. The new verification details are returned in
    plaintext for demonstration; a real implementation should email
    them to the user.
    """
    # Normalise the email before processing; errors bubble up from
    # ``start_verification`` if the user does not exist or is already
    # verified.
    verification = start_verification(email.strip().lower())
    return {
        "message": "Verification code has been resent.",
        "verification": {
            "otp": verification.get("otp"),
            "token": verification.get("token"),
            "expires_at": verification.get("expires_at"),
        },
    }


@app.post("/auth/verify-email", tags=["User"])
def verify_email(payload: VerifyEmailPayload) -> Dict[str, Any]:
    """Verify a user's email address via OTP or token.

    Clients may supply either the OTP or the verification token. If
    neither is provided, a 400 error is returned. The helper
    ``verify_user`` performs all validation and marks the email as
    verified. On success, a sanitized user record is returned.
    """
    if not payload.otp and not payload.token:
        raise HTTPException(status_code=400, detail="Provide either otp or token")
    user = verify_user(
        email=payload.email.strip().lower(),
        otp=payload.otp,
        token=payload.token,
    )
    return {
        "message": "Email verified successfully.",
        "user": sanitize_user(user),
    }


@app.post("/auth/login", tags=["User"])
def login(payload: LoginPayload) -> Dict[str, Any]:
    """Authenticate a user and issue a session token.

    The user's email must be verified before login is permitted. On
    success, a session token is generated and returned along with a
    sanitized user record. Session tokens expire after 24 hours by
    default; clients should store them securely and include them in
    subsequent requests via the ``X-SESSION-TOKEN`` header or
    ``session_token`` query parameter.
    """
    user = authenticate_user(
        email=payload.email.strip().lower(), password=payload.password
    )
    session_rec = create_session(user_id=user["id"])
    return {
        "message": "Login successful.",
        "session_token": session_rec["token"],
        "expires_at": session_rec["expires_at"],
        "user": sanitize_user(user),
    }


@app.post("/auth/logout", tags=["User"])
def logout(request: Request) -> Dict[str, Any]:
    """Revoke an active session token.

    Clients should call this endpoint to invalidate their session
    token upon logout. The token is extracted from the
    ``X-SESSION-TOKEN`` header or ``session_token`` query parameter. If
    the token cannot be found or is already revoked/expired, no
    exception is thrown; the operation is idempotent.
    """
    token = request.headers.get("X-SESSION-TOKEN") or request.query_params.get(
        "session_token"
    )
    if token:
        revoke_session(token)
    return {"message": "Logged out successfully."}


@app.get("/user", tags=["User"])
def get_current_user(user: Dict[str, Any] = Depends(session_required)) -> Dict[str, Any]:
    """Return the current user's profile (sanitized).

    Requires a valid session token. Only verified users can call this
    endpoint. The returned record omits sensitive fields such as
    password hashes and salts.
    """
    return {"user": sanitize_user(user)}


@app.post("/auth/create-key", tags=["User"])
def create_key(
    payload: CreateKeyPayload,
    user: Dict[str, Any] = Depends(session_required),
) -> Dict[str, Any]:
    """Create a new API key for the current user.

    Users may request either a free or paid key. For free keys, the
    ``generate_free_key`` helper is invoked with the user's id as the
    owner. Paid keys are created via ``generate_api_key`` which
    defaults to unlimited credits until a subscription plan is
    applied. The resulting raw key string is returned. Clients are
    responsible for storing the key securely.

    Args:
        payload: A ``CreateKeyPayload`` specifying the desired tier.
        user: The authenticated user record resolved via the session.

    Returns:
        A dictionary containing the ``tier`` and the newly created
        ``api_key``.

    Raises:
        HTTPException: If the tier is invalid.
    """
    tier_lower = payload.tier.strip().lower()
    email_raw = payload.email or user.get("email") or ""
    first_raw = payload.first_name or user.get("first_name") or ""
    last_raw = payload.last_name or user.get("last_name") or ""
    email, first_name, last_name = _normalize_contact_fields(email_raw, first_raw, last_raw)
    if tier_lower not in {"free", "paid"}:
        raise HTTPException(status_code=400, detail="Invalid key tier specified")
    if tier_lower == "free":
        raw_key = generate_free_key(
            owner=user["id"],
            email=email,
            first_name=first_name,
            last_name=last_name,
        )
        return {
            "tier": "free",
            "api_key": raw_key,
            "contact": {
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
            },
        }
    else:
        # For paid keys, set owner to the user's id. The key will
        # default to unlimited credits until the user upgrades via
        # subscription. Users can later apply a plan using the
        # `/billing/upgrade` endpoint with their key.
        raw_key = generate_api_key(
            owner=user["id"],
            email=email,
            first_name=first_name,
            last_name=last_name,
            active=False,
            subscription_status="pending",
        )
        return {
            "tier": "paid",
            "api_key": raw_key,
            "contact": {
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
            },
            "active": False,
            "subscription_status": "pending",
            "note": "Attach a subscription using /billing/upgrade or /billing/subscribe to enable paid limits. The key stays inactive until billing completes.",
        }


@app.post("/user/keys/revoke", tags=["User"])
def revoke_my_key(
    payload: RevokeSelfPayload,
    user: Dict[str, Any] = Depends(session_required),
) -> Dict[str, Any]:
    """Revoke a key owned by the logged-in user after password confirmation."""
    authenticate_user(email=user["email"], password=payload.password)
    _get_user_key_record_or_error(payload.api_key, user["id"])
    revoke_api_key(payload.api_key)
    return {"status": "revoked", "message": "API key has been disabled."}


@app.post("/user/keys/regenerate", tags=["User"])
def regenerate_my_key(
    payload: RegenerateKeyPayload,
    user: Dict[str, Any] = Depends(session_required),
) -> Dict[str, Any]:
    """Rotate a user's API key (invalidates the old key) after password confirmation."""
    authenticate_user(email=user["email"], password=payload.password)
    record = _get_user_key_record_or_error(payload.api_key, user["id"])
    new_raw, updated_record = rotate_api_key(payload.api_key)
    return {
        "message": "API key rotated. Store the new key securely; the old key no longer works.",
        "api_key": new_raw,
        "tier": updated_record.get("tier"),
        "plan_code": updated_record.get("plan_code"),
        "subscription_status": updated_record.get("subscription_status"),
        "contact": {
            "email": record.get("email"),
            "first_name": record.get("first_name"),
            "last_name": record.get("last_name"),
        },
    }


@app.post("/user/keys/revoke-by-prefix", tags=["User"])
def revoke_my_key_by_prefix(
    payload: RevokeByPrefixPayload,
    user: Dict[str, Any] = Depends(session_required),
) -> Dict[str, Any]:
    """Revoke a key using its prefix (for users who misplaced the raw key)."""
    authenticate_user(email=user["email"], password=payload.password)
    record = _get_user_key_by_prefix(user["id"], payload.key_prefix)
    _revoke_key_record(record)
    return {"status": "revoked", "key_prefix": payload.key_prefix + "..."}


@app.post("/user/keys/regenerate-by-prefix", tags=["User"])
def regenerate_my_key_by_prefix(
    payload: RegenerateByPrefixPayload,
    user: Dict[str, Any] = Depends(session_required),
) -> Dict[str, Any]:
    """Rotate a key using its prefix (no need to provide the raw key)."""
    authenticate_user(email=user["email"], password=payload.password)
    record = _get_user_key_by_prefix(user["id"], payload.key_prefix)
    new_raw, updated_record = _rotate_key_record(record)
    return {
        "message": "API key rotated. Store the new key securely; the old key no longer works.",
        "api_key": new_raw,
        "tier": updated_record.get("tier"),
        "plan_code": updated_record.get("plan_code"),
        "subscription_status": updated_record.get("subscription_status"),
        "key_prefix": (updated_record.get("key_hash") or "")[:8] + ".",
        "contact": {
            "email": updated_record.get("email"),
            "first_name": updated_record.get("first_name"),
            "last_name": updated_record.get("last_name"),
        },
    }


@app.get("/user/keys", tags=["User"])
def list_user_keys(user: Dict[str, Any] = Depends(session_required)) -> Dict[str, Any]:
    """List all API keys owned by the current user.

    Filters the ``keys.json`` store by the ``owner`` field matching
    the user's id. Only active keys are returned. The ``key_prefix``
    field exposes the first eight characters of the key hash for
    reference. Note that raw keys are never retrievable once created;
    users must store them securely when first generated.
    """
    data = load_keys()
    owned = []
    for record in data.get("keys", []):
        if record.get("owner") == user["id"]:
            owned.append(
                {
                    "tier": record.get("tier"),
                    "active": record.get("active", True),
                    "revoked": bool(record.get("revoked_at")),
                    "plan_code": record.get("plan_code"),
                    "pending_plan_code": record.get("pending_plan_code"),
                    "subscription_status": record.get("subscription_status"),
                    "email": record.get("email"),
                    "first_name": record.get("first_name"),
                    "last_name": record.get("last_name"),
                    "created_at": to_wat_iso(record.get("created_at")),
                    "revoked_at": to_wat_iso(record.get("revoked_at")),
                    "upgraded_at": to_wat_iso(record.get("upgraded_at")),
                    "key_prefix": record.get("key_hash", "")[:8] + ".",
                }
            )
    return {"keys": owned}


@app.get("/usage", tags=["Utility"])
def usage_snapshot(key: Dict[str, Any] = Depends(api_key_required)) -> Dict[str, Any]:
    """Return usage details for the calling API key."""
    usage_map = get_usage_map()
    tracked = usage_map.get(key.get("key_hash"), {}) or {}
    per_minute, per_day = _rate_limits_for_record(key)
    return {"usage": _compose_usage_entry(key, tracked, per_minute, per_day)}


@app.get("/user/usage", tags=["User"])
def list_user_usage(user: Dict[str, Any] = Depends(session_required)) -> Dict[str, Any]:
    """Return usage details for all keys owned by the session user."""
    data = load_keys()
    usage_map = get_usage_map()
    summaries = []
    for record in data.get("keys", []):
        if record.get("owner") != user["id"]:
            continue
        per_minute, per_day = _rate_limits_for_record(record)
        tracked = usage_map.get(record.get("key_hash"), {}) or {}
        summaries.append(_compose_usage_entry(record, tracked, per_minute, per_day))
    return {"usage": summaries}


# ---------------------------------------------------------------------------
# Transaction history endpoints
# ---------------------------------------------------------------------------

@app.get("/transactions", tags=["Billing"])
def list_my_transactions(key: Dict[str, Any] = Depends(api_key_required)) -> Dict[str, Any]:
    """Return the transaction history for the calling user's API key.

    This endpoint lists all payment transactions associated with the
    provided API key. It is intended for non-admin users to view
    their own subscription payment history. Transactions include
    pending, successful and failed statuses.
    """
    txns = get_transactions_for_key(key["key_hash"])
    return {"transactions": txns}


@app.get("/admin/transactions", tags=["Admin"])
def list_all_transactions(_: bool = Depends(admin_required)) -> Dict[str, Any]:
    """Return the transaction history for all API keys (admin only).

    Admins can use this endpoint to view every recorded payment
    transaction across all keys. The list is not filtered or masked.
    """
    txns = load_transactions().get("transactions", [])
    return {"transactions": txns}


@app.get("/admin/keys", tags=["Admin"])
def admin_list_keys(_: bool = Depends(admin_required)) -> Dict[str, Any]:
    """Admin endpoint to list all API keys (with masked hashes)."""
    data = load_keys()
    keys_out = []
    for key in data.get("keys", []):
        keys_out.append(
            {
                "owner": key.get("owner"),
                "active": key.get("active", True),
                "revoked": bool(key.get("revoked_at")),
                "tier": key.get("tier", "paid"),
                "plan_code": key.get("plan_code"),
                "pending_plan_code": key.get("pending_plan_code"),
                "subscription_status": key.get("subscription_status"),
                "email": key.get("email"),
                "first_name": key.get("first_name"),
                "last_name": key.get("last_name"),
                "paystack_subscription_code": key.get("paystack_subscription_code"),
                # Convert numeric timestamps to ISO strings for display
                "created_at": to_wat_iso(key.get("created_at")),
                "upgraded_at": to_wat_iso(key.get("upgraded_at")),
                "revoked_at": to_wat_iso(key.get("revoked_at")),
                "key_prefix": key.get("key_hash", "")[:8] + ".",
            }
        )
    return {"keys": keys_out}


@app.get("/openapi.json", include_in_schema=False)
async def openapi_json(_: Dict[str, Any] = Depends(api_key_required)) -> JSONResponse:
    """Serve the OpenAPI schema, protected by API key."""
    global _cached_openapi
    try:
        _cached_openapi  # type: ignore
    except NameError:
        _cached_openapi = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
    return JSONResponse(_cached_openapi)


@app.get("/docs", include_in_schema=False)
async def swagger_docs(_: Dict[str, Any] = Depends(api_key_required)) -> HTMLResponse:
    """Serve Swagger UI, protected by API key."""
    return get_swagger_ui_html(
        openapi_url="/openapi.json", title="Currency Conversion API Docs"
    )

# ---------------------------------------------------------------------------
# Public documentation endpoints
# ---------------------------------------------------------------------------

# We expose a read-only OpenAPI schema and docs without requiring an API key.
# These endpoints are useful for developers exploring the API before obtaining
# a key. They are intentionally excluded from the generated schema via
# ``include_in_schema=False`` to avoid cluttering the spec with duplicate
# entries.

@app.get("/public/openapi.json", include_in_schema=False)
async def public_openapi_json() -> JSONResponse:
    """Serve the OpenAPI schema without authentication."""
    global _public_openapi
    try:
        return JSONResponse(_public_openapi)  # type: ignore
    except NameError:
        _public_openapi = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        return JSONResponse(_public_openapi)


@app.get("/public/docs", include_in_schema=False)
async def public_swagger_ui() -> HTMLResponse:
    """Serve a public Swagger UI that does not require an API key."""
    return get_swagger_ui_html(
        title="Currency Conversion API Docs (Public)",
        openapi_url="/public/openapi.json",
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist/swagger-ui.css",
    )


@app.get("/public/redoc", include_in_schema=False)
async def public_redoc() -> HTMLResponse:
    """Serve a public ReDoc interface that does not require an API key."""
    return get_redoc_html(
        title="Currency Conversion API Docs (ReDoc)",
        openapi_url="/public/openapi.json",
    )
