"""JSON logging and per-request access log middleware (pure ASGI)."""
import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.errors import error_response

SENSITIVE_KEYS = {"password", "refresh_token", "access_token"}
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
MAX_LOGGED_BODY = 10_000

logger = logging.getLogger("marketplace.api")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
        }
        fields = getattr(record, "fields", None)
        if fields:
            entry.update(fields)
        else:
            entry["message"] = record.getMessage()
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


def mask(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: "***" if k.lower() in SENSITIVE_KEYS else mask(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v) for v in value]
    return value


def _body_for_log(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return mask(json.loads(raw))
    except (ValueError, UnicodeDecodeError):
        return f"<non-JSON body, {len(raw)} bytes>"


class RequestLoggingMiddleware:
    """Generates request_id, exposes it as X-Request-Id and writes one JSON log line per request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid.uuid4())
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        started_at = datetime.now(timezone.utc)
        started = time.perf_counter()
        capture_body = scope["method"] in MUTATING_METHODS
        body = bytearray()
        status_code = 500
        response_started = False

        async def receive_wrapper() -> Message:
            message = await receive()
            if capture_body and message["type"] == "http.request" and len(body) < MAX_LOGGED_BODY:
                body.extend(message.get("body", b""))
            return message

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                MutableHeaders(scope=message).append("X-Request-Id", request_id)
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except Exception:
            logger.exception("Unhandled error", extra={"fields": {"request_id": request_id, "message": "Unhandled error"}})
            if not response_started:
                await error_response(500, "INTERNAL_ERROR", "Internal server error")(scope, receive, send_wrapper)
        finally:
            fields = {
                "request_id": request_id,
                "method": scope["method"],
                "endpoint": scope["path"],
                "status_code": status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "user_id": state.get("user_id"),
                "timestamp": started_at.isoformat(),
            }
            if capture_body:
                fields["request_body"] = _body_for_log(bytes(body))
            logger.info("request", extra={"fields": fields})
