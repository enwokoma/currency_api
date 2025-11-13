from fastapi import FastAPI, Request, Depends, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse, JSONResponse, HTMLResponse
from fastapi.openapi.utils import get_openapi
from fastapi.openapi.docs import get_swagger_ui_html

import orjson
import time

from auth import (
    validate_api_key,
    generate_api_key,
    revoke_api_key,
    load_keys
)

from rate_limit import allow_request
from utils import validate_currency, fetch_rates

ADMIN_TOKEN = "change-this-admin-token"
openapi_schema = None


# ==================================================================
# FASTAPI APP
# ==================================================================

app = FastAPI(
    title="Currency Conversion API",
    description="Fast API with API keys, rate limits, fallback provider, pretty JSON, logging.",
    version="2.0.0",
    default_response_class=ORJSONResponse,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_credentials=True,
    allow_headers=["*"]
)


# ==================================================================
# MIDDLEWARE: Logging + Pretty JSON
# ==================================================================

@app.middleware("http")
async def request_logger(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = round((time.time() - start) * 1000, 2)
    print(f"[{request.method}] {request.url.path} {response.status_code} {duration}ms")
    return response


@app.middleware("http")
async def prettify_json(request: Request, call_next):
    resp = await call_next(request)
    if hasattr(resp, "body"):
        try:
            data = orjson.loads(resp.body)
            resp.body = orjson.dumps(data, option=orjson.OPT_INDENT_2)
        except:
            pass
    return resp


# ==================================================================
# AUTH HELPERS
# ==================================================================

def api_key_required(request: Request):
    raw = request.headers.get("X-API-KEY") or request.query_params.get("api_key")
    key = validate_api_key(raw)
    allow_request(key["key_hash"])
    return key


def admin_required(request: Request):
    token = request.headers.get("X-ADMIN-TOKEN") or request.query_params.get("admin_token")
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid admin token")
    return True


# ==================================================================
# HEALTH
# ==================================================================

@app.get("/health")
def health():
    return {"status": "ok"}


# ==================================================================
# CORE ENDPOINTS
# ==================================================================

@app.get("/rate")
def get_rate(
    from_currency: str = Query(..., alias="from"),
    to_currency: str = Query(..., alias="to"),
    _: dict = Depends(api_key_required)
):
    from_currency = validate_currency(from_currency)
    to_currency = validate_currency(to_currency)

    rates = fetch_rates(from_currency)
    if to_currency not in rates:
        raise HTTPException(status_code=400, detail="Unsupported target currency")

    return {
        "from": from_currency.upper(),
        "to": to_currency.upper(),
        "rate": rates[to_currency]
    }


@app.get("/convert")
def convert_currency(
    from_currency: str = Query(..., alias="from"),
    to_currency: str = Query(..., alias="to"),
    amount: float = Query(...),
    _: dict = Depends(api_key_required)
):
    if amount < 0:
        raise HTTPException(status_code=400, detail="Amount must be ≥ 0")

    from_currency = validate_currency(from_currency)
    to_currency = validate_currency(to_currency)

    rates = fetch_rates(from_currency)
    if to_currency not in rates:
        raise HTTPException(status_code=400, detail="Unsupported target currency")

    converted = round(amount * rates[to_currency], 4)

    return {
        "from": from_currency.upper(),
        "to": to_currency.upper(),
        "amount": amount,
        "rate": rates[to_currency],
        "converted_amount": converted
    }


# ==================================================================
# ADMIN: KEY MANAGEMENT
# ==================================================================

@app.post("/admin/generate-key")
def admin_generate_key(owner: str = Body(...), _: bool = Depends(admin_required)):
    raw = generate_api_key(owner)
    return {"owner": owner, "api_key": raw}


@app.post("/admin/revoke-key")
def admin_revoke_key(api_key: str = Body(...), _: bool = Depends(admin_required)):
    revoke_api_key(api_key)
    return {"status": "ok", "message": "API key revoked"}


@app.get("/admin/keys")
def admin_list_keys(_: bool = Depends(admin_required)):
    data = load_keys()
    return {
        "keys": [
            {
                "owner": k["owner"],
                "active": k["active"],
                "created_at": k["created_at"],
                "key_prefix": k["key_hash"][:8] + "…"
            }
            for k in data["keys"]
        ]
    }


# ==================================================================
# PROTECTED SWAGGER DOCS
# ==================================================================

@app.get("/openapi.json", include_in_schema=False)
def get_openapi_json(_: dict = Depends(api_key_required)):
    global openapi_schema
    if openapi_schema is None:
        openapi_schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
    return JSONResponse(openapi_schema)


@app.get("/docs", include_in_schema=False)
def swagger_docs(_: dict = Depends(api_key_required)):
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="Currency API Docs"
    )
