"""Time utilities for converting timestamps to WAT ISO strings."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

# West Africa Time (Lagos) does not observe daylight savings.
WAT = timezone(timedelta(hours=1))


def to_wat_iso(value: Any) -> Any:
    """
    Convert a numeric timestamp (epoch) to a West Africa Time ISO string without timezone.

    Non-numeric values are returned unchanged.
    """
    if isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value, WAT)
        naive = dt.replace(tzinfo=None)
        return naive.isoformat(timespec="milliseconds")
    return value
