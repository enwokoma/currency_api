"""Currency Conversion API using FastAPI.

This API wraps the open‐source Exchange API maintained by Fawaz Ahmed to
provide quick conversion rates between world currencies. Rates are
cached for six hours to minimize network requests. All responses are
pretty‑printed JSON.
"""

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import ORJSONResponse
from cachetools import TTLCache
import requests
import orjson

app = FastAPI(
    title="Currency Conversion API",
    description=(
        "Free, unlimited currency conversion service built on top of "
        "the Exchange‑API by Fawaz Ahmed. Supports over 200 currencies "
        "including many cryptocurrencies and metals. Rates are refreshed "
        "daily and cached for six hours."
    ),
    version="1.0.0",
    default_response_class=ORJSONResponse,
)

# Cache up to 500 base‑currency lookups for six hours. Exchange‑API
# publishes daily JSON files, so caching longer than six hours is safe.
cache: TTLCache[str, dict] = TTLCache(maxsize=500, ttl=60 * 60 * 6)

BASE_URL = (
    "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies"
)


def fetch_rates(base: str) -> dict:
    """Fetch conversion rates for a given base currency.

    Args:
        base: Three‑letter ISO currency code (e.g. "usd").

    Returns:
        Dictionary mapping target currency codes to their rates relative
        to the base currency.

    Raises:
        HTTPException: If the upstream API does not return a JSON
            document for the requested currency or if the currency is
            unsupported.
    """
    base = base.lower()
    # Return from cache if available
    if base in cache:
        return cache[base]

    url = f"{BASE_URL}/{base}.json"
    response = requests.get(url, timeout=10)
    if response.status_code != 200:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid base currency '{base.upper()}' or network error.",
        )
    data = response.json()
    rates = data.get(base)
    if not isinstance(rates, dict):
        raise HTTPException(
            status_code=400,
            detail=f"Currency '{base.upper()}' not found in upstream API.",
        )
    # Store in cache and return
    cache[base] = rates
    return rates


def pretty_json(obj: dict) -> str:
    """Serialize a Python object to indented JSON using orjson."""
    return orjson.dumps(obj, option=orjson.OPT_INDENT_2).decode()


@app.middleware("http")
async def prettify_json(request: Request, call_next):
    """Middleware to pretty‑print ORJSONResponse bodies.

    FastAPI with ORJSONResponse returns compact JSON by default. This
    middleware intercepts responses and re‑serializes them with
    indentation for readability. Non‑JSON responses are passed
    untouched.
    """
    response = await call_next(request)
    if isinstance(response, ORJSONResponse):
        try:
            # Decode original body and re‑encode with indentation
            data = orjson.loads(response.body)
            pretty = pretty_json(data)
            response.body = pretty.encode()
        except Exception:
            # If decoding fails, leave the response untouched
            pass
    return response


@app.get("/rate")
def get_rate(
    from_currency: str = Query(..., alias="from", description="Base currency code"),
    to_currency: str = Query(..., alias="to", description="Target currency code"),
) -> dict:
    """Return the conversion rate from one currency to another.

    Example:
        /rate?from=USD&to=NGN

    Raises:
        HTTPException: If the target currency is unsupported.
    """
    from_lower = from_currency.lower()
    to_lower = to_currency.lower()
    rates = fetch_rates(from_lower)
    if to_lower not in rates:
        raise HTTPException(
            status_code=400,
            detail=f"Target currency '{to_currency.upper()}' is not supported.",
        )
    rate = rates[to_lower]
    return {
        "from": from_currency.upper(),
        "to": to_currency.upper(),
        "rate": rate,
    }


@app.get("/convert")
def convert_currency(
    from_currency: str = Query(..., alias="from", description="Base currency code"),
    to_currency: str = Query(..., alias="to", description="Target currency code"),
    amount: float = Query(..., description="Amount to convert"),
) -> dict:
    """Convert an amount from one currency into another.

    Example:
        /convert?from=EUR&to=USD&amount=50

    Raises:
        HTTPException: If the target currency is unsupported.
    """
    from_lower = from_currency.lower()
    to_lower = to_currency.lower()
    rates = fetch_rates(from_lower)
    if to_lower not in rates:
        raise HTTPException(
            status_code=400,
            detail=f"Target currency '{to_currency.upper()}' is not supported.",
        )
    rate = rates[to_lower]
    converted = amount * rate
    return {
        "from": from_currency.upper(),
        "to": to_currency.upper(),
        "amount": amount,
        "rate": rate,
        "converted_amount": round(converted, 4),
    }