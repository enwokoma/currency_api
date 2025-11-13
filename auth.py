import json
import os
import time
import uuid
import hashlib
from fastapi import HTTPException

KEY_FILE = "keys.json"

# Ensure keys file exists
if not os.path.exists(KEY_FILE):
    with open(KEY_FILE, "w") as f:
        json.dump({"keys": []}, f, indent=2)


# ------------------------------------------
# Helper: Load & Save Keys
# ------------------------------------------

def load_keys():
    with open(KEY_FILE, "r") as f:
        return json.load(f)


def save_keys(data):
    with open(KEY_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ------------------------------------------
# Helper: Secure Hash
# ------------------------------------------

def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


# ------------------------------------------
# Generate API Key
# ------------------------------------------

def generate_api_key(owner: str):
    raw = str(uuid.uuid4())
    hashed = hash_key(raw)

    data = load_keys()
    data["keys"].append({
        "key_hash": hashed,
        "owner": owner,
        "active": True,
        "created_at": time.time()
    })
    save_keys(data)

    return raw


# ------------------------------------------
# Validate API Key (Auth)
# ------------------------------------------

def validate_api_key(raw_key: str):
    if not raw_key:
        raise HTTPException(status_code=401, detail="Missing API Key")

    hashed = hash_key(raw_key)
    data = load_keys()

    for key in data["keys"]:
        if key["key_hash"] == hashed:
            if not key.get("active", True):
                raise HTTPException(status_code=403, detail="API key revoked")

            key_copy = key.copy()
            key_copy["key_hash"] = hashed
            return key_copy

    raise HTTPException(status_code=401, detail="Invalid API key")


# ------------------------------------------
# Revoke API Key
# ------------------------------------------

def revoke_api_key(raw_key: str):
    hashed = hash_key(raw_key)
    data = load_keys()

    for key in data["keys"]:
        if key["key_hash"] == hashed:
            key["active"] = False
            save_keys(data)
            return True

    raise HTTPException(status_code=404, detail="API key not found")


# ------------------------------------------
# Free-Tier Key
# ------------------------------------------

def generate_free_key():
    raw = "free_" + uuid.uuid4().hex[:16]
    hashed = hash_key(raw)

    data = load_keys()
    data["keys"].append({
        "key_hash": hashed,
        "owner": "free-tier",
        "tier": "free",
        "active": True,
        "created_at": time.time()
    })
    save_keys(data)

    return raw
