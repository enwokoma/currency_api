"""Append-only log of raw Paystack webhook deliveries.

Each delivery is written as one JSON object per line (newline-delimited
JSON), so the file can be appended to safely without re-reading it. Use
``beautify_logs.py`` to produce a pretty-printed copy for inspection; it
writes to a separate file so this log stays appendable.

Only deliveries with a valid signature are logged with their body. Refused
ones are recorded as a reason and a size, so unauthenticated requests cannot
write arbitrary data to disk.
"""

import json
from datetime import datetime, timezone

LOG_FILE = "webhook_logs.json"


def _append(entry: dict) -> None:
    try:
        line = json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), **entry})
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        print(f"Failed to log webhook event: {e}")


def log_webhook_event(request_body: bytes) -> None:
    """Append a verified webhook body to the NDJSON log."""
    _append({"body": request_body.decode("utf-8", errors="replace")})


def log_rejected_webhook(size: int, reason: str) -> None:
    """Record a refused delivery without its body."""
    _append({"rejected": reason, "size": size})
