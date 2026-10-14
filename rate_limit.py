"""
Rate limiting logic for the currency conversion service.

This module tracks requests per bucket (an API key hash, or a route and
client address for unauthenticated routes) and enforces per‑minute and
per‑day limits. The numeric limits are passed in by the caller.

Request timestamps live in a SQLite database next to the JSON stores
(``RATE_LIMIT_DB``, default ``rate_limits.sqlite3``), so every worker process
on the host shares the same counters and they survive restarts. Each check
runs in an IMMEDIATE transaction: SQLite's file lock lets only one process at
a time read a bucket and take a slot, so two workers cannot both take the
last one.
"""

from __future__ import annotations

import ipaddress
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Iterator, Optional

from fastapi import HTTPException

DB_PATH = os.getenv("RATE_LIMIT_DB", "rate_limits.sqlite3")

# Hits older than this are never counted and are deleted.
RETENTION_SECONDS = 24 * 60 * 60

# Buckets keyed by client address come from unauthenticated traffic, so hits
# past the retention window are purged from every bucket this often (calls).
SWEEP_EVERY = 1000
_calls_since_sweep = 0

# One connection per process, shared by handler threads under this lock.
_LOCK = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _connection() -> sqlite3.Connection:
    """Open the shared database on first use (relative to the working directory)."""
    global _conn
    if _conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("CREATE TABLE IF NOT EXISTS hits (bucket TEXT NOT NULL, ts REAL NOT NULL)")
        conn.execute("CREATE INDEX IF NOT EXISTS hits_bucket_ts ON hits (bucket, ts)")
        _conn = conn
    return _conn


@contextmanager
def _transaction() -> Iterator[sqlite3.Connection]:
    """Run a read-modify-write on the hits table atomically across processes."""
    with _LOCK:
        conn = _connection()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")


def client_bucket(host: str | None) -> str:
    """Name the rate-limit bucket for a client address.

    IPv6 clients are grouped by /64, the block a single host usually holds,
    so rotating addresses within it does not reset the limit.
    """
    if not host:
        return "unknown"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host
    if ip.version == 6:
        if ip.ipv4_mapped:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def _acquire(bucket: str, per_minute: int | None, per_day: int | None) -> Optional[str]:
    """Take a slot in ``bucket`` unless a limit is reached.

    Returns ``None`` when the slot was taken, else ``"minute"`` or ``"day"``
    for the limit that refused it. Refused attempts are not recorded.
    """
    global _calls_since_sweep
    now = time.time()
    with _transaction() as conn:
        _calls_since_sweep += 1
        if _calls_since_sweep >= SWEEP_EVERY:
            _calls_since_sweep = 0
            conn.execute("DELETE FROM hits WHERE ts <= ?", (now - RETENTION_SECONDS,))
        conn.execute("DELETE FROM hits WHERE bucket = ? AND ts <= ?", (bucket, now - RETENTION_SECONDS))
        if per_minute is not None:
            (recent,) = conn.execute(
                "SELECT COUNT(*) FROM hits WHERE bucket = ? AND ts > ?", (bucket, now - 60)
            ).fetchone()
            if recent >= per_minute:
                return "minute"
        if per_day is not None:
            (today,) = conn.execute("SELECT COUNT(*) FROM hits WHERE bucket = ?", (bucket,)).fetchone()
            if today >= per_day:
                return "day"
        conn.execute("INSERT INTO hits (bucket, ts) VALUES (?, ?)", (bucket, now))
        return None


def allow_request(
    key_hash: str,
    per_minute: int,
    per_day: int,
) -> None:
    """Enforce rate limits for a bucket, usually an API key hash.

    Args:
        key_hash: The bucket to count against.
        per_minute: Number of allowed requests in the last 60 seconds.
        per_day: Number of allowed requests in the last 24 hours.

    Raises:
        HTTPException: With status 429 if either limit is exceeded.
    """
    refused = _acquire(key_hash, per_minute, per_day)
    if refused == "minute":
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded (per minute). Please slow down.",
        )
    if refused == "day":
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded (per day). Please try again later.",
        )


def try_acquire(bucket: str, per_minute: int | None, per_day: int | None) -> bool:
    """Like ``allow_request`` but returns False instead of raising."""
    return _acquire(bucket, per_minute, per_day) is None


def count_hits(bucket: str, window_seconds: float) -> int:
    """Number of hits recorded in ``bucket`` during the last ``window_seconds``."""
    with _transaction() as conn:
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM hits WHERE bucket = ? AND ts > ?", (bucket, time.time() - window_seconds)
        ).fetchone()
    return count


def record_hit(bucket: str) -> None:
    """Record one hit in ``bucket`` without checking any limit."""
    with _transaction() as conn:
        conn.execute("INSERT INTO hits (bucket, ts) VALUES (?, ?)", (bucket, time.time()))


def clear_bucket(bucket: str) -> None:
    """Forget every hit recorded in ``bucket``."""
    with _transaction() as conn:
        conn.execute("DELETE FROM hits WHERE bucket = ?", (bucket,))


def reset() -> None:
    """Forget every hit in every bucket (tests and maintenance)."""
    with _transaction() as conn:
        conn.execute("DELETE FROM hits")
