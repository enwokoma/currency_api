"""Main application module for the currency conversion API.

This file wires together all components of the service, including
authentication, rate limiting, billing, and the core currency
conversion endpoints. All requests (except health and free key
creation) require a valid API key. Admin endpoints are secured by an
admin token. Swagger and OpenAPI documentation are also locked
behind API authentication.
"""

from __future__ import annotations

import time
import json
from typing import Dict, Any, Optional

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
    status,
)
from fastapi.middleware.cors import CORSMiddleware
# Import JSONResponse and HTMLResponse only. ORJSONResponse is
# conditionally aliased above based on the availability of orjson.
from fastapi.responses import JSONResponse, HTMLResponse  # noqa: F401
from fastapi.openapi.utils import get_openapi
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html

from pydantic import BaseModel

from auth import load_keys, upgrade_key_tier_by_hash
from dependencies import api_key_required, paid_key_required
from utils import validate_currency, fetch_rates, fetch_historical_rates
from billing import (
    verify_paystack_signature,
    verify_transaction_reference,
    extract_webhook_info,
)
from transactions import (
    update_transactions_for_key,
    load_transactions,
    get_transactions_for_key,
    find_transaction_by_reference,
    update_transaction_by_reference,
)
from config import PLANS, FREE_TIER_MONTHLY_CREDITS
from billing_routes import billing_router
from auth_routes import auth_router
from user_routes import user_router
from admin_routes import admin_router

from webhook_logs import log_webhook_event

# Try to load environment variables from a .env file if python-dotenv is installed.
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except ImportError:
    # python-dotenv is optional; environment variables can still be provided
    pass



from time_utils import to_wat_iso


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
# Internal helpers
# ---------------------------------------------------------------------------

 


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

# Mount routers
app.include_router(billing_router)
app.include_router(auth_router)
app.include_router(user_router)
app.include_router(admin_router)


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
