"""Shared helpers for JSON file persistence."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from functools import wraps
from typing import Any, Callable, Dict, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

# Every store is mutated with a load -> modify -> save cycle. Sync route
# handlers run concurrently in a threadpool, so two requests for the same key
# could read the same balance and each write it back minus one, losing a
# decrement. A single process-wide re-entrant lock serialises those cycles:
# the primitives below take it for every read and write, and ``synchronized``
# wraps whole read-modify-write functions. It is re-entrant so a locked
# function may call another locked function. Reads must hold it too because
# on Windows ``os.replace`` fails while another thread has the file open.
#
# Note: this protects a single process only. Run the service with one worker.
STORE_LOCK = threading.RLock()


def synchronized(func: F) -> F:
    """Run ``func`` while holding ``STORE_LOCK``."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with STORE_LOCK:
            return func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


def _backup_corrupt_file(path: str, content: str | None = None) -> None:
    """Write a best-effort backup of a corrupt JSON file."""
    if not os.path.exists(path):
        return
    timestamp = time.strftime("%Y%m%d%H%M%S")
    backup_path = f"{path}.corrupt.{timestamp}"
    try:
        if content is None:
            with open(path, "rb") as fh:
                blob = fh.read()
        else:
            blob = content.encode("utf-8", errors="replace")
        with open(backup_path, "wb") as fh:
            fh.write(blob)
    except OSError:
        # Best-effort backup; ignore if we cannot write it.
        pass


@synchronized
def atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    """Write JSON to disk via a temp file to avoid partial writes."""
    dirpath = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".",
        suffix=".tmp",
        dir=dirpath,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


@synchronized
def ensure_json_file(path: str, root_key: str) -> None:
    """Ensure a JSON file exists and is parseable, else reinitialize it."""
    if not os.path.exists(path):
        atomic_write_json(path, {root_key: []})
        return
    content: str | None = None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            content = fh.read()
        if not content.strip():
            raise ValueError("Empty file")
        json.loads(content)
    except (ValueError, json.JSONDecodeError):
        _backup_corrupt_file(path, content)
        atomic_write_json(path, {root_key: []})


@synchronized
def load_json_file(path: str, root_key: str) -> Dict[str, Any]:
    """Load JSON from disk, resetting if the content is invalid."""
    ensure_json_file(path, root_key)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError:
        _backup_corrupt_file(path)
        atomic_write_json(path, {root_key: []})
        return {root_key: []}
