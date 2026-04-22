#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request


SCRIPT_DIR = Path(__file__).resolve().parent


def load_dotenv_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip("\"'")


load_dotenv_file(SCRIPT_DIR / ".env")


def env(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    return value.strip()


def resolve_local_path(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = SCRIPT_DIR / path
    return path.resolve()


WEBHOOK_SECRET = env("KENSO_WEBHOOK_SECRET")
WEBHOOK_PATH = env("KENSO_WEBHOOK_PATH", "/webhooks/knownsense")
WEBHOOK_HOST = env("KENSO_WEBHOOK_HOST", "127.0.0.1")
WEBHOOK_PORT = int(env("KENSO_WEBHOOK_PORT", "8787"))
MAX_SKEW_SECONDS = int(env("KENSO_WEBHOOK_MAX_SKEW_SECONDS", "300"))
EVENT_LOG = resolve_local_path(env("KENSO_WEBHOOK_EVENT_LOG", "./data/received_events.jsonl"))
DATA_DIR = EVENT_LOG.parent

if not WEBHOOK_SECRET:
    raise SystemExit("KENSO_WEBHOOK_SECRET is required")


app = Flask(__name__)
seen_event_ids: set[str] = set()


def parse_signature_header(header: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for item in header.split(","):
        item = item.strip()
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        parts[key.strip()] = value.strip()
    return parts


def verify_signature(raw_body: bytes, headers: Any, signing_secret: str) -> tuple[str, str]:
    event_id = headers.get("X-Kenso-Event-ID", "").strip()
    timestamp = headers.get("X-Kenso-Timestamp", "").strip()
    signature_header = headers.get("X-Kenso-Signature", "").strip()

    if not event_id:
        raise ValueError("Missing X-Kenso-Event-ID header")
    if not timestamp or not signature_header:
        raise ValueError("Missing webhook signature headers")
    if not timestamp.isdigit():
        raise ValueError("Invalid X-Kenso-Timestamp header")

    now = int(time.time())
    ts = int(timestamp)
    if abs(now - ts) > MAX_SKEW_SECONDS:
        raise ValueError("Webhook timestamp is outside the allowed skew window")

    parts = parse_signature_header(signature_header)
    received = parts.get("v1", "")
    if not received:
        raise ValueError("Missing v1 signature")

    signed_payload = timestamp.encode("utf-8") + b"." + raw_body
    expected = hmac.new(
        signing_secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(received, expected):
        raise ValueError("Invalid KnownSense webhook signature")

    return event_id, headers.get("X-Kenso-Event-Type", "").strip()


def append_event(record: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with EVENT_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")


@app.get("/health")
def health() -> Response:
    return jsonify(
        {
            "status": "ok",
            "path": WEBHOOK_PATH,
            "event_log": str(EVENT_LOG),
        }
    )


@app.post(WEBHOOK_PATH)
def knownsense_webhook() -> Response:
    raw_body = request.get_data(cache=False)

    try:
        event_id, event_type = verify_signature(raw_body, request.headers, WEBHOOK_SECRET)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    if event_id in seen_event_ids:
        return jsonify({"ok": True, "duplicate": True, "event_id": event_id})

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError:
        return jsonify({"ok": False, "error": "Invalid JSON payload"}), 400

    seen_event_ids.add(event_id)
    record = {
        "received_at_unix": int(time.time()),
        "event_id": event_id,
        "event_type": event_type,
        "headers": {
            "X-Kenso-Event-ID": request.headers.get("X-Kenso-Event-ID", ""),
            "X-Kenso-Event-Type": request.headers.get("X-Kenso-Event-Type", ""),
            "X-Kenso-Timestamp": request.headers.get("X-Kenso-Timestamp", ""),
            "X-Kenso-Signature": request.headers.get("X-Kenso-Signature", ""),
        },
        "payload": payload,
    }
    append_event(record)

    print(f"[receiver] verified {event_type} event_id={event_id}")
    print(json.dumps(payload, indent=2, ensure_ascii=True))

    return jsonify({"ok": True, "event_id": event_id, "event_type": event_type})


if __name__ == "__main__":
    print(f"[receiver] listening on http://{WEBHOOK_HOST}:{WEBHOOK_PORT}{WEBHOOK_PATH}")
    app.run(host=WEBHOOK_HOST, port=WEBHOOK_PORT, debug=False)
