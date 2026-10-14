"""Health, docs, startup export, storage resilience, concurrency, utilities."""

from __future__ import annotations

import glob
import json
import os
from concurrent.futures import ThreadPoolExecutor

import helpers
from helpers import admin_key, free_key, rate_call, send_webhook, usage


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_json_is_pretty_printed_and_errors_use_detail(client):
    assert client.get("/health").text.startswith("{\n  ")
    r = client.post("/rate", json={"from_currency": "USD", "to_currency": "NGN"})
    assert r.status_code == 401
    assert r.json() == {"detail": "Missing API Key"}


def test_public_docs_need_no_key(client):
    for path in ("/public/openapi.json", "/public/docs", "/public/redoc"):
        assert client.get(path).status_code == 200, path
    schema = client.get("/public/openapi.json").json()
    assert schema["info"]["version"] == helpers.main.APP_VERSION
    assert {"/rate", "/convert", "/rates", "/billing/subscribe", "/paystack/webhook"} <= set(schema["paths"])


def test_protected_docs_require_a_key_but_never_charge(client):
    assert client.get("/docs").status_code == 401
    assert client.get("/openapi.json", headers={"X-API-KEY": "nope"}).status_code == 401
    key = free_key(client, "docs@example.com")
    for _ in range(3):
        assert client.get("/docs", headers={"X-API-KEY": key}).status_code == 200
        assert client.get("/openapi.json", headers={"X-API-KEY": key}).status_code == 200
    u = usage(client, key)
    assert u["total_requests"] == 1  # only the /usage call itself
    assert u["remaining_credits"] == 199
    assert client.get("/openapi.json", headers={"X-API-KEY": key}).json() == client.get("/public/openapi.json").json()


def test_startup_regenerates_postman_collection(client):
    with open("postman_collection.json", encoding="utf-8") as fh:
        collection = json.load(fh)
    folders = {f["name"]: f for f in collection["item"]}
    assert {"Billing", "Auth", "User", "Utility", "Core", "Admin"} <= set(folders)
    urls = {item["request"]["url"]["raw"] for f in collection["item"] for item in f["item"]}
    assert "{{base_url}}/user/keys/claim" in urls
    assert "{{base_url}}/me/usage" not in urls
    rate_item = next(i for i in folders["Core"]["item"] if i["request"]["url"]["raw"] == "{{base_url}}/rate")
    assert {"key": "Content-Type", "value": "application/json"} in rate_item["request"]["header"]
    assert collection["info"]["version"] == helpers.main.APP_VERSION


def test_corrupt_store_is_backed_up_and_reset(client):
    with open("keys.json", "w", encoding="utf-8") as fh:
        fh.write("{this is not json")
    key = free_key(client, "recover@example.com")
    assert helpers.key_record(key)["email"] == "recover@example.com"
    assert glob.glob("keys.json.corrupt.*"), "corrupt file should be preserved as a backup"


def test_empty_store_is_reinitialised(client):
    open("users.json", "w", encoding="utf-8").close()
    assert helpers.register(client, "empty@example.com").status_code == 202


def test_atomic_writes_leave_no_temp_files(client):
    free_key(client, "tmp@example.com")
    assert not glob.glob("*.tmp")


def test_concurrent_charges_are_exact(client):
    key = admin_key(client, "load", "load@example.com", plan_code="PRO_MONTHLY")
    n = 60
    with ThreadPoolExecutor(max_workers=16) as pool:
        codes = list(pool.map(lambda _: rate_call(client, key).status_code, range(n)))
    assert codes.count(200) == n
    u = usage(client, key)
    assert u["remaining_credits"] == helpers.plan("PRO_MONTHLY")["monthly_credits"] - n - 1
    assert u["total_requests"] == n + 1


def test_to_wat_iso_formats_epoch_and_passes_strings_through():
    from time_utils import to_wat_iso

    assert to_wat_iso(0) == "1970-01-01T01:00:00.000"
    assert to_wat_iso("2025-01-01T12:00:00.000") == "2025-01-01T12:00:00.000"
    assert to_wat_iso(None) is None


def test_webhook_log_is_ndjson_and_beautifier_writes_separate_file(client):
    assert send_webhook(client, "charge.success", {}, signature="bad").status_code == 400
    assert send_webhook(client, "transfer.success", {"x": 1}).status_code == 200
    with open("webhook_logs.json", encoding="utf-8") as fh:
        raw = fh.read()
    lines = [json.loads(line) for line in raw.strip().splitlines()]
    assert len(lines) == 2, "every delivery leaves a line"
    assert lines[0]["rejected"] == "invalid signature" and "body" not in lines[0], "an unsigned body is never stored"

    import beautify_logs

    assert beautify_logs.beautify_webhook_logs() == 2
    with open("webhook_logs.pretty.json", encoding="utf-8") as fh:
        pretty = json.load(fh)
    assert pretty[1]["body"] == {"event": "transfer.success", "data": {"x": 1}}
    with open("webhook_logs.json", encoding="utf-8") as fh:
        assert fh.read() == raw, "source log must stay untouched and appendable"
    assert os.path.exists("webhook_logs.pretty.json")
