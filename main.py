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
from typing import Dict, Any, Optional

import orjson
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
from fastapi.responses import ORJSONResponse, JSONResponse, HTMLResponse
from fastapi.openapi.utils import get_openapi
from fastapi.openapi.docs import get_swagger_ui_html

from pydantic import BaseModel

from auth import (
    validate_api_key,
    generate_api_key,
    generate_free_key,
    revoke_api_key,
    load_keys,
    upgrade_key_tier_by_hash,
)
from rate_limit import allow_request
from utils import validate_currency, fetch_rates
from billing import (
    initialize_transaction,
    verify_paystack_signature,
    extract_webhook_info,
)

from dotenv import load_dotenv
load_dotenv()


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

    token = "admin_token_" + secrets.token_hex(16)
    with open(ADMIN_FILE, "w", encoding="utf-8") as fh:
        fh.write(token)
    print(
        "\n=============================================\n"
        " 🔐 NEW ADMIN TOKEN GENERATED\n"
        f" Your Admin Token: {token}\n"
        " (Saved to admin_token.txt)\n"
        "=============================================\n"
    )
    return token


ADMIN_TOKEN = _load_or_create_admin_token()


# Subscription plans. Update this list to match the plans created in
# your Paystack dashboard. Each plan defines the price in Naira and
# the rate limits (per minute and per day). These limits are applied
# automatically when a key has the corresponding plan code.
PLANS = [
    {
        "code": "BASIC_MONTHLY",
        "name": "Basic Monthly",
        "price_naira": 2000,
        "currency": "NGN",
        "requests_per_minute": 100,
        "requests_per_day": 10_000,
    },
    {
        "code": "PRO_MONTHLY",
        "name": "Pro Monthly",
        "price_naira": 5000,
        "currency": "NGN",
        "requests_per_minute": 300,
        "requests_per_day": 50_000,
    },
]


# Pydantic models for request bodies
class OwnerPayload(BaseModel):
    owner: str


class RevokePayload(BaseModel):
    api_key: str


class UpgradePayload(BaseModel):
    email: str
    plan_code: str


# FastAPI app configuration
app = FastAPI(
    title="Currency Conversion API",
    description=(
        "Fast, cached currency conversion API using Fawaz Ahmed's Exchange API "
        "with fallback provider, API keys, rate limiting, free and paid tiers, "
        "and Paystack subscription upgrades."
    ),
    version="3.0.0",
    default_response_class=ORJSONResponse,
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


@app.middleware("http")
async def prettify_json(request: Request, call_next):
    resp = await call_next(request)
    if hasattr(resp, "body"):
        try:
            data = orjson.loads(resp.body)
            resp.body = orjson.dumps(data, option=orjson.OPT_INDENT_2)
        except Exception:
            pass
    return resp


# Dependency: enforce API key presence and rate limiting
def api_key_required(request: Request) -> Dict[str, Any]:
    raw_key = request.headers.get("X-API-KEY") or request.query_params.get("api_key")
    key_record = validate_api_key(raw_key)
    # Determine tier and plan
    tier = key_record.get("tier", "paid")
    plan_code = key_record.get("plan_code")
    allow_request(key_record["key_hash"], tier=tier, plan_code=plan_code)
    return key_record


# Dependency: enforce admin token on admin endpoints
def admin_required(request: Request) -> bool:
    token = request.headers.get("X-ADMIN-TOKEN") or request.query_params.get(
        "admin_token"
    )
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid admin token")
    return True


@app.get("/health", tags=["Utility"])
def health() -> Dict[str, str]:
    """Health check endpoint (no authentication required)."""
    return {"status": "ok"}


@app.get("/rate", tags=["Core"], response_model=Dict[str, Any])
def get_rate(
    from_currency: str = Query(..., alias="from"),
    to_currency: str = Query(..., alias="to"),
    _: Dict[str, Any] = Depends(api_key_required),
) -> Dict[str, Any]:
    """Retrieve the conversion rate from one currency to another."""
    from_lower = validate_currency(from_currency)
    to_lower = validate_currency(to_currency)
    rates = fetch_rates(from_lower)
    if to_lower not in rates:
        raise HTTPException(
            status_code=400,
            detail=f"Target currency '{to_currency.upper()}' is not supported",
        )
    return {
        "from": from_currency.upper(),
        "to": to_currency.upper(),
        "rate": rates[to_lower],
    }


@app.get("/convert", tags=["Core"], response_model=Dict[str, Any])
def convert_currency(
    from_currency: str = Query(..., alias="from"),
    to_currency: str = Query(..., alias="to"),
    amount: float = Query(...),
    _: Dict[str, Any] = Depends(api_key_required),
) -> Dict[str, Any]:
    """Convert an amount from one currency to another."""
    if amount < 0:
        raise HTTPException(status_code=400, detail="Amount must be non‑negative")
    from_lower = validate_currency(from_currency)
    to_lower = validate_currency(to_currency)
    rates = fetch_rates(from_lower)
    if to_lower not in rates:
        raise HTTPException(
            status_code=400,
            detail=f"Target currency '{to_currency.upper()}' is not supported",
        )
    rate = rates[to_lower]
    converted_amount = round(amount * rate, 4)
    return {
        "from": from_currency.upper(),
        "to": to_currency.upper(),
        "amount": amount,
        "rate": rate,
        "converted_amount": converted_amount,
    }


@app.post("/auth/free-key", tags=["Auth"])
def get_free_key() -> Dict[str, str]:
    """Public endpoint to request a free‑tier API key."""
    raw = generate_free_key()
    return {
        "tier": "free",
        "api_key": raw,
        "note": "Free keys have limited quota. Upgrade for higher limits.",
    }


@app.get("/plans", tags=["Billing"])
def list_plans() -> Dict[str, Any]:
    """Public endpoint to list available subscription plans."""
    return {"plans": PLANS}


@app.post("/billing/upgrade", tags=["Billing"], response_model=Dict[str, Any])
def billing_upgrade(
    payload: UpgradePayload,
    key: Dict[str, Any] = Depends(api_key_required),
) -> Dict[str, Any]:
    """Start a Paystack transaction to upgrade a free key to a paid plan.

    The caller must supply an email address and a plan_code that exists
    in the ``PLANS`` list. The API key used for authentication will
    determine which key is upgraded.
    """
    # Ensure the plan_code is valid
    plan = next((p for p in PLANS if p["code"] == payload.plan_code), None)
    if not plan:
        raise HTTPException(status_code=400, detail="Unknown plan_code")
    # Start Paystack transaction
    auth_url, reference = initialize_transaction(
        email=payload.email,
        amount_naira=plan["price_naira"],
        api_key_hash=key["key_hash"],
        plan_code=payload.plan_code,
    )
    return {
        "authorization_url": auth_url,
        "reference": reference,
        "plan": plan,
    }


@app.post("/paystack/webhook", status_code=status.HTTP_200_OK, tags=["Billing"])
async def paystack_webhook(request: Request) -> Dict[str, str]:
    """Handle Paystack webhook events for subscription management."""
    body = await request.body()
    signature = request.headers.get("x-paystack-signature")
    if not verify_paystack_signature(body, signature):
        raise HTTPException(status_code=400, detail="Invalid Paystack signature")
    event_type, api_key_hash, plan_code = extract_webhook_info(body)
    # Successful subscription or charge events should upgrade the key to paid
    if event_type in ("charge.success", "subscription.create", "subscription.enable"):
        if api_key_hash and plan_code:
            upgrade_key_tier_by_hash(api_key_hash, new_tier="paid", plan_code=plan_code)
    # Disabled or failed subscriptions should downgrade the key to free
    if event_type in ("subscription.disable", "invoice.failed", "invoice.create_failed"):
        if api_key_hash:
            upgrade_key_tier_by_hash(api_key_hash, new_tier="free", plan_code=None)
    return {"status": "ok"}


@app.post("/admin/generate-key", tags=["Admin"])
def admin_generate_key(
    payload: OwnerPayload,
    _: bool = Depends(admin_required),
) -> Dict[str, str]:
    """Admin endpoint to generate a new paid API key."""
    raw = generate_api_key(payload.owner)
    return {"owner": payload.owner, "api_key": raw}


@app.post("/admin/revoke-key", tags=["Admin"])
def admin_revoke_key(
    payload: RevokePayload,
    _: bool = Depends(admin_required),
) -> Dict[str, str]:
    """Admin endpoint to revoke an existing API key."""
    revoke_api_key(payload.api_key)
    return {"status": "ok", "message": "API key revoked"}


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
                "tier": key.get("tier", "paid"),
                "plan_code": key.get("plan_code"),
                "created_at": key.get("created_at"),
                "key_prefix": key.get("key_hash", "")[:8] + "…",
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