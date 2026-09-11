"""Currency endpoints: current, historical, validation and provider fallbacks."""

from __future__ import annotations

from helpers import admin_key, free_key, rate_call


def _post(client, key, path, body):
    return client.post(path, headers={"X-API-KEY": key}, json=body)


def test_rate_convert_and_rates(client):
    key = free_key(client, "core@example.com")
    r = rate_call(client, key, "usd", "ngn")
    assert r.status_code == 200 and r.json() == {"from": "USD", "to": "NGN", "rate": 1500.0}

    r = _post(client, key, "/convert", {"from_currency": "EUR", "to_currency": "USD", "amount": 50})
    assert r.status_code == 200
    assert r.json() == {"from": "EUR", "to": "USD", "amount": 50.0, "rate": 1.1, "converted_amount": 55.0}

    r = _post(client, key, "/rates", {"base_currency": "usd"})
    assert r.status_code == 200
    assert r.json()["base"] == "USD" and r.json()["rates"]["ngn"] == 1500.0 and r.json()["rates"]["usd"] == 1.0


def test_currency_code_validation(client):
    key = free_key(client, "val@example.com")
    for bad in ("US", "USDX", "12A", ""):
        r = rate_call(client, key, bad, "NGN")
        assert r.status_code == 400 and "3 letters" in r.json()["detail"], bad
    r = rate_call(client, key, "USD", "XYZ")
    assert r.status_code == 400 and "not supported" in r.json()["detail"]
    r = _post(client, key, "/rates", {"base_currency": "xyz"})
    assert r.status_code == 200 and r.json()["rates"] == {"xyz": 1.0}, "unknown base falls through to the self-rate"
    assert _post(client, key, "/rate", {"from_currency": "USD"}).status_code == 422


def test_convert_amount_rules(client):
    key = free_key(client, "amt@example.com")
    r = _post(client, key, "/convert", {"from_currency": "USD", "to_currency": "NGN", "amount": -1})
    assert r.status_code == 400 and "non-negative" in r.json()["detail"]
    r = _post(client, key, "/convert", {"from_currency": "USD", "to_currency": "NGN", "amount": 0})
    assert r.status_code == 200 and r.json()["converted_amount"] == 0.0
    r = _post(client, key, "/convert", {"from_currency": "GBP", "to_currency": "EUR", "amount": 3.3333})
    assert r.json()["converted_amount"] == round(3.3333 * 1.13, 4)


def test_historical_endpoints(client, rates):
    key = admin_key(client, "h", "h@example.com", plan_code="PRO_MONTHLY")
    r = _post(client, key, "/rate/historical", {"from_currency": "USD", "to_currency": "NGN", "date": "2024-12-31"})
    assert r.status_code == 200
    assert r.json() == {"from": "USD", "to": "NGN", "date": "2024-12-31", "rate": 3000.0}
    assert "@2024-12-31/" in rates.calls[-1]

    r = _post(client, key, "/convert/historical", {"from_currency": "GBP", "to_currency": "USD", "date": "2024-12-15", "amount": 10})
    assert r.status_code == 200 and r.json()["converted_amount"] == 25.0 and r.json()["date"] == "2024-12-15"

    r = _post(client, key, "/rates/historical", {"base_currency": "EUR", "date": "2024-06-01"})
    assert r.status_code == 200 and r.json()["rates"]["usd"] == 2.2 and r.json()["base"] == "EUR"

    r = _post(client, key, "/rate/historical", {"from_currency": "USD", "to_currency": "NGN", "date": "31-12-2024"})
    assert r.status_code == 400 and "YYYY-MM-DD" in r.json()["detail"]
    r = _post(client, key, "/rate/historical", {"from_currency": "USD", "to_currency": "NGN", "date": "2999-01-01"})
    assert r.status_code == 400 and "future" in r.json()["detail"]
    r = _post(client, key, "/convert/historical", {"from_currency": "USD", "to_currency": "NGN", "date": "2024-12-31", "amount": -5})
    assert r.status_code == 400


def test_provider_fallback_chain(client, rates):
    key = admin_key(client, "fb", "fb@example.com", plan_code="PRO_MONTHLY")

    rates.primary_ok = False
    r = rate_call(client, key)
    assert r.status_code == 200 and r.json()["rate"] == 1500.0
    assert "cdn.jsdelivr.net" in rates.calls[-2] and "currency-api.pages.dev" in rates.calls[-1]

    r = _post(client, key, "/rate/historical", {"from_currency": "USD", "to_currency": "NGN", "date": "2024-12-31"})
    assert r.status_code == 200 and rates.calls[-1].startswith("https://2024-12-31.currency-api.pages.dev/")

    rates.mirror_ok = False
    r = rate_call(client, key)
    assert r.status_code == 200 and r.json()["rate"] == 1630.0, "static table is the last resort"
    r = rate_call(client, key, "EUR", "USD")
    assert r.status_code == 400 and "not supported" in r.json()["detail"], "static table only knows USD/NGN"
