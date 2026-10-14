"""Fixtures: isolated stores per test, mocked Paystack, mocked rate providers."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any, Dict, Optional

import pytest
from fastapi.testclient import TestClient

import helpers  # noqa: F401  (bootstraps cwd + environment before app imports)
from helpers import STORES, write_store
import rate_limit
from services import billing_service, rate_service


class FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class PaystackMock:
    """Stands in for api.paystack.co. Records calls and serves scripted answers."""

    def __init__(self) -> None:
        self.calls = []
        self.init_fail = False
        self.disable_fail = False
        self.init_count = 0
        self.initialized: Dict[str, Dict[str, Any]] = {}
        self.disabled = []
        self.verify: Dict[str, Dict[str, Any]] = {}
        self.subscriptions = [
            {"subscription_code": "SUB_basic", "email_token": "ETK_basic", "status": "active", "plan": {"plan_code": "PLN_basic"}},
            {"subscription_code": "SUB_pro", "email_token": "ETK_pro", "status": "active", "plan": {"plan_code": "PLN_pro"}},
        ]

    def mark_success(
        self,
        reference: str,
        api_key_hash: Optional[str],
        plan_code: Optional[str],
        *,
        status: str = "success",
        customer_id: Optional[int] = 1,
        email: str = "payer@example.com",
        amount_kobo: Optional[int] = None,
        subscription: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Script what GET /transaction/verify/{reference} returns."""
        plan_def = next((p for p in helpers.PLANS if p["code"] == plan_code), None)
        amount = amount_kobo if amount_kobo is not None else (plan_def or {}).get("price_naira", 0) * 100
        meta: Dict[str, Any] = {"email": email}
        if api_key_hash is not None:
            meta["api_key_hash"] = api_key_hash
        if plan_code is not None:
            meta["plan_code"] = plan_code
        if metadata is not None:
            meta = metadata
        txn = {
            "status": status,
            "reference": reference,
            "amount": amount,
            "metadata": meta,
            "customer": {"id": customer_id, "email": email} if customer_id is not None else {"email": email},
        }
        if subscription:
            txn["subscription"] = subscription
        self.verify[reference] = txn
        return txn

    # requests.post stand-in
    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("POST", url, json))
        if url.endswith("/transaction/initialize"):
            if self.init_fail:
                return FakeResponse({"status": False, "message": "Invalid plan"}, 400)
            self.init_count += 1
            ref = f"ref_{self.init_count}"
            self.initialized[ref] = json
            return FakeResponse(
                {"status": True, "data": {"authorization_url": f"https://checkout.paystack.com/{ref}", "reference": ref}}
            )
        if url.endswith("/subscription/disable"):
            self.disabled.append(json)
            if self.disable_fail:
                return FakeResponse({"status": False, "message": "Subscription not found"}, 404)
            return FakeResponse({"status": True, "message": "Subscription disabled successfully"})
        raise AssertionError(f"unexpected Paystack POST {url}")

    # requests.get stand-in
    def get(self, url, headers=None, timeout=None):
        self.calls.append(("GET", url, None))
        if "/transaction/verify/" in url:
            ref = url.rsplit("/", 1)[1]
            txn = self.verify.get(ref)
            if txn is None:
                return FakeResponse({"status": False, "message": "Transaction reference not found"}, 404)
            return FakeResponse({"status": True, "data": txn})
        if "/subscription?customer=" in url:
            return FakeResponse({"status": True, "data": self.subscriptions})
        raise AssertionError(f"unexpected Paystack GET {url}")


RATES = {
    "usd": {"usd": 1.0, "ngn": 1500.0, "eur": 0.9, "gbp": 0.8},
    "eur": {"eur": 1.0, "usd": 1.1, "ngn": 1650.0, "gbp": 0.88},
    "gbp": {"gbp": 1.0, "usd": 1.25, "eur": 1.13, "ngn": 1875.0},
}


class RatesMock:
    """Stands in for the currency-api CDN and its mirror. Historical rates are doubled."""

    def __init__(self) -> None:
        self.calls = []
        self.primary_ok = True
        self.mirror_ok = True

    def get(self, url, timeout=None):
        self.calls.append(url)
        is_primary = "cdn.jsdelivr.net" in url
        if is_primary and not self.primary_ok:
            return FakeResponse({}, 503)
        if not is_primary and not self.mirror_ok:
            raise ConnectionError("mirror unreachable")
        base = url.rsplit("/", 1)[1][: -len(".json")]
        table = RATES.get(base)
        if not table:
            return FakeResponse({}, 404)
        historical = "@latest" not in url and not url.startswith("https://latest.")
        if historical:
            table = {k: (v * 2 if k != base else v) for k, v in table.items()}
        return FakeResponse({"date": "2025-01-01", base: table})


@pytest.fixture(autouse=True)
def clean_state():
    """Fresh stores, empty rate-limiter and no webhook logs for every test."""
    for name, root in STORES.items():
        write_store(name, {root: []})
    for name in ("webhook_logs.json", "webhook_logs.pretty.json"):
        if os.path.exists(name):
            os.remove(name)
    rate_limit.reset()
    yield


@pytest.fixture(autouse=True)
def paystack(monkeypatch) -> PaystackMock:
    mock = PaystackMock()
    monkeypatch.setattr(billing_service, "requests", SimpleNamespace(post=mock.post, get=mock.get))
    return mock


@pytest.fixture(autouse=True)
def rates(monkeypatch) -> RatesMock:
    mock = RatesMock()
    monkeypatch.setattr(rate_service, "requests", SimpleNamespace(get=mock.get))
    return mock


@pytest.fixture
def client():
    with TestClient(helpers.main.app) as c:
        yield c
