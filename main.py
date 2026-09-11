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
from typing import Any, Dict

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

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
# Import JSONResponse and HTMLResponse only. ORJSONResponse is
# conditionally aliased above based on the availability of orjson.
from fastapi.responses import JSONResponse, HTMLResponse  # noqa: F401
from fastapi.openapi.utils import get_openapi
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html

from dependencies import api_key_required
from routers.admin import admin_router
from routers.auth import auth_router
from routers.billing import billing_router
from routers.core import core_router
from routers.users import user_router
from postman_exporter import export_postman_collection

# Cache containers for generated OpenAPI docs (populated lazily).
_cached_openapi: Dict[str, Any] | None = None
_public_openapi: Dict[str, Any] | None = None

# Try to load environment variables from a .env file if python-dotenv is installed.
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except ImportError:
    # python-dotenv is optional; environment variables can still be provided
    pass




# ---------------------------------------------------------------------------
# FastAPI metadata (reused for OpenAPI generation)
APP_TITLE = "Currency Conversion API"
APP_DESCRIPTION = (
    "Fast, cached currency conversion API using Fawaz Ahmed's Exchange API "
    "with fallback provider, API keys, rate limiting, free and paid tiers, "
    "and Paystack subscription upgrades."
)
APP_VERSION = "3.1.0"

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
    title=APP_TITLE,
    description=APP_DESCRIPTION,
    version=APP_VERSION,
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
app.include_router(core_router)
app.include_router(admin_router)

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    # Startup event
    try:
        export_postman_collection(fastapi_app)
    except Exception as exc:
        print(f"[postman-export] Failed to refresh collection: {exc}")
    yield


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

app.lifespan = lifespan


@app.get("/openapi.json", include_in_schema=False)
async def openapi_json(_: Dict[str, Any] = Depends(api_key_required)) -> JSONResponse:
    """Serve the OpenAPI schema, protected by API key."""
    global _cached_openapi
    if _cached_openapi is None:
        _cached_openapi = get_openapi(
            title=APP_TITLE,
            version=APP_VERSION,
            description=APP_DESCRIPTION,
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
    if _public_openapi is None:
        _public_openapi = get_openapi(
            title=APP_TITLE,
            version=APP_VERSION,
            description=APP_DESCRIPTION,
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
