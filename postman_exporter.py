"""Generate a Postman collection from the FastAPI app's OpenAPI schema."""

from __future__ import annotations

import json
from typing import Any, Dict, List


def _build_request(path: str, method: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    url = {
        "raw": "{{base_url}}" + path,
        "host": ["{{base_url}}"],
        "path": [segment for segment in path.strip("/").split("/") if segment] or [""],
    }
    # Query params
    query_params: List[Dict[str, str]] = []
    for param in spec.get("parameters", []):
        if param.get("in") == "query":
            query_params.append(
                {
                    "key": param.get("name", ""),
                    "value": "",
                    "description": param.get("description", ""),
                }
            )
    if query_params:
        url["query"] = query_params

    request: Dict[str, Any] = {
        "name": spec.get("summary") or f"{method.upper()} {path}",
        "method": method.upper(),
        "header": [],
        "url": url,
        "description": spec.get("description") or "",
    }
    # Basic JSON body template
    body = spec.get("requestBody", {})
    content = body.get("content", {}) if isinstance(body, dict) else {}
    json_schema = content.get("application/json", {})
    if json_schema:
        request["body"] = {
            "mode": "raw",
            "raw": json.dumps(json_schema.get("example", {}), indent=2) if json_schema.get("example") else "",
            "options": {"raw": {"language": "json"}},
        }
        request["header"].append({"key": "Content-Type", "value": "application/json"})
    return request


def export_postman_collection(app, output_path: str = "postman_collection.json") -> None:
    """Export the FastAPI OpenAPI schema into a Postman 2.1 collection."""
    schema = app.openapi()
    info = schema.get("info", {})
    collection: Dict[str, Any] = {
        "info": {
            "name": info.get("title", "Currency Conversion API"),
            "description": info.get("description", ""),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            "version": info.get("version", "1.0.0"),
        },
        "item": [],
        "variable": [
            {"key": "base_url", "value": "http://localhost:8000", "type": "string"},
        ],
    }

    folders: Dict[str, Dict[str, Any]] = {}
    for path, methods in schema.get("paths", {}).items():
        for method, spec in methods.items():
            if method.lower() == "parameters":
                continue
            tags = spec.get("tags") or ["General"]
            request = _build_request(path, method, spec)
            for tag in tags:
                folder = folders.setdefault(tag, {"name": tag, "item": []})
                folder["item"].append({"name": request["name"], "request": request, "response": []})
    collection["item"] = list(folders.values())

    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(collection, fh, indent=2)
