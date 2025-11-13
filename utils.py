import requests
from fastapi import HTTPException

PRIMARY = (
    "https://cdn.jsdelivr.net/npm/"
    "@fawazahmed0/currency-api@latest/v1/currencies"
)
FALLBACK = "https://api.exchangerate.host/latest?base={base}"


# Validate 3-letter currency format
def validate_currency(code: str) -> str:
    code = code.strip().lower()
    if len(code) != 3 or not code.isalpha():
        raise HTTPException(status_code=400, detail="Currency must be 3 letters (A-Z)")
    return code


# Fetch rates with primary → fallback
def fetch_rates(base: str):
    base = validate_currency(base)

    # Primary provider
    try:
        url = f"{PRIMARY}/{base}.json"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json().get(base)
            if data:
                return {k.lower(): float(v) for k, v in data.items()}
    except:
        pass

    # Fallback provider
    try:
        url = FALLBACK.format(base=base.upper())
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json().get("rates")
            if data:
                return {k.lower(): float(v) for k, v in data.items()}
    except:
        pass

    raise HTTPException(status_code=502, detail="FX sources unavailable")
