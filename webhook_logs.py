"""Append-only log of raw Paystack webhook deliveries.

Each delivery is written as one JSON object per line (newline-delimited
JSON), so the file can be appended to safely without re-reading it. Use
``beautify_logs.py`` to produce a pretty-printed copy for inspection; it
writes to a separate file so this log stays appendable.
"""

import json
from datetime import datetime, timezone

LOG_FILE = "webhook_logs.json"


def log_webhook_event(request_body: bytes) -> None:
    """Append the raw webhook body to the NDJSON log."""
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            log_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "body": request_body.decode("utf-8", errors="replace"),
            }
            f.write(json.dumps(log_entry) + "\n")
    except Exception as e:
        print(f"Failed to log webhook event: {e}")
