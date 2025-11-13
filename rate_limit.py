import time
from fastapi import HTTPException

# request_timestamps[key_hash] = [timestamps...]
request_timestamps = {}

RATE_LIMIT_PER_MINUTE = 100
RATE_LIMIT_PER_DAY = 10000


def allow_request(key_hash: str, tier="paid"):
    now = time.time()

    if key_hash not in request_timestamps:
        request_timestamps[key_hash] = []

    timestamps = [t for t in request_timestamps[key_hash] if now - t < 86400]

    last_minute = len([t for t in timestamps if now - t < 60])

    # Free tier limits
    if tier == "free":
        if last_minute >= 10:
            raise HTTPException(status_code=429, detail="Free-tier limit: 10 requests/minute")
        if len(timestamps) >= 200:
            raise HTTPException(status_code=429, detail="Free-tier limit: 200 requests/day")
    else:
        # Paid tier limits
        if last_minute >= 100:
            raise HTTPException(status_code=429, detail="Paid-tier limit: 100 requests/minute")
        if len(timestamps) >= 10000:
            raise HTTPException(status_code=429, detail="Paid-tier limit: 10,000 requests/day")

    timestamps.append(now)
    request_timestamps[key_hash] = timestamps
