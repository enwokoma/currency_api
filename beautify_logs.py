"""Produce a readable copy of the webhook log.

``webhook_logs.json`` is newline-delimited JSON written by ``webhook_logs.py``.
This script parses each line, expands the nested ``body`` string into an
object, and writes the result as an indented JSON array to a separate output
file (``webhook_logs.pretty.json`` by default). It never rewrites the source
log: doing so would turn it into a single array, and later appends would then
produce a file that is neither valid NDJSON nor valid JSON.
"""

import json
import sys

DEFAULT_INPUT = "webhook_logs.json"
DEFAULT_OUTPUT = "webhook_logs.pretty.json"


def _iter_entries(content: str):
    """Yield log entries from NDJSON content, or from a legacy JSON array."""
    stripped = content.strip()
    if not stripped:
        return
    if stripped.startswith("["):
        # A file previously rewritten as an array by an older version of this tool.
        yield from json.loads(stripped)
        return
    for line in stripped.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"Skipping invalid JSON line: {line[:80]!r} - {exc}")


def beautify_webhook_logs(input_path: str = DEFAULT_INPUT, output_path: str = DEFAULT_OUTPUT) -> int:
    """Write a pretty-printed array of log entries to ``output_path``.

    Returns the number of entries written.
    """
    with open(input_path, "r", encoding="utf-8") as f:
        content = f.read()

    entries = []
    for entry in _iter_entries(content):
        if isinstance(entry, dict) and isinstance(entry.get("body"), str):
            try:
                entry["body"] = json.loads(entry["body"])
            except json.JSONDecodeError:
                pass  # keep the raw string if the body was not JSON
        entries.append(entry)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=4)
    return len(entries)


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INPUT
    dst = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUTPUT
    count = beautify_webhook_logs(src, dst)
    print(f"Wrote {count} entries to {dst}")
