"""Admin token utilities."""

from __future__ import annotations

import os
import secrets

import env  # noqa: F401  # ensure .env is loaded before reading os.getenv

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
    token = "admin_token_" + secrets.token_hex(32)
    with open(ADMIN_FILE, "w", encoding="utf-8") as fh:
        fh.write(token)
    print(
        "\n=============================================\n"
        "  NEW ADMIN TOKEN GENERATED\n"
        f" Your Admin Token: {token}\n"
        " (Saved to admin_token.txt)\n"
        "=============================================\n"
    )
    return token


ADMIN_TOKEN = _load_or_create_admin_token()


def get_expected_admin_token() -> str:
    """Return the expected admin token (prefers env override)."""
    return os.getenv("ADMIN_TOKEN") or ADMIN_TOKEN
