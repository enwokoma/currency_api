import time
from fastapi import HTTPException

# request_timestamps[key_hash] = [timestamps...]
request_timestamps = {}

RATE_LIMIT_PER_MINUTE = 100
RATE_LIMIT_PER_DAY = 10000


def allow_request(key_hash: str):
    now = time.time()

    if key_hash not in request_timestamps:
        request_timestamps[key_hash] = []

    # Keep timestamps within the last 24 hours
    timestamps = [t for t in request_timestamps[key_hash] if now - t < 86400]

    # Count last minute
    last_minute = len([t for t in timestamps if now - t < 60])

    if last_minute >= RATE_LIMIT_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Rate limit exceeded (per minute)")

    if len(timestamps) >= RATE_LIMIT_PER_DAY:
        raise HTTPException(status_code=429, detail="Rate limit exceeded (per day)")

    timestamps.append(now)
    request_timestamps[key_hash] = timestamps
