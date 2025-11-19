"""Load environment variables from a .env file when python-dotenv is available."""

from __future__ import annotations


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        return
    load_dotenv()


_load_dotenv_if_available()
