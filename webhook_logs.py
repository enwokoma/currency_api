import json
from datetime import datetime

LOG_FILE = "webhook_logs.json"

def log_webhook_event(request_body: bytes):
    """Logs the raw webhook event to a file."""
    try:
        with open(LOG_FILE, "a") as f:
            log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "body": request_body.decode("utf-8"),
            }
            f.write(json.dumps(log_entry) + "\n")
    except Exception as e:
        print(f"Failed to log webhook event: {e}")
