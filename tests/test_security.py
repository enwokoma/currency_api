"""Security regressions: key-hash leaks, billing authorisation and hardening."""

from __future__ import annotations

from helpers import (
    activate_via_webhook,
    free_key,
    hash_key,
    key_record,
    register_and_login,
    subscribe,
    upgrade,
)


def _paid_victim(client, paystack, email="victim@example.com") -> str:
    key = subscribe(client, email).json()["api_key"]
    activate_via_webhook(client, paystack, key, "BASIC_MONTHLY", "ref_1")
    return key


def test_duplicate_email_errors_do_not_reveal_the_key_hash(client, paystack):
    victim = _paid_victim(client, paystack)
    victim_hash = hash_key(victim)

    # Any caller holding a public free key can try to move its contact email onto the victim's.
    attacker_key = free_key(client, "attacker@example.com")
    r = upgrade(client, attacker_key, "BASIC_MONTHLY", email="victim@example.com", first_name="M", last_name="X")
    assert r.status_code == 400 and "already associated" in r.json()["detail"]
    assert victim_hash[:4] not in r.text

    # Any logged-in user can ask /auth/create-key for the victim's email.
    session = register_and_login(client, "mallory@example.com")
    for tier in ("free", "paid"):
        r = client.post("/auth/create-key", headers=session, json={"tier": tier, "email": "victim@example.com"})
        assert r.status_code == 400 and "already associated" in r.json()["detail"]
        assert victim_hash[:4] not in r.text, tier
