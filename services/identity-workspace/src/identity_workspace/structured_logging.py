"""Per-application JSON logging with a closed field/value allowlist."""

from collections.abc import Callable
from datetime import datetime, timezone
import json
import logging
import math
from typing import TextIO
from uuid import UUID

from identity_workspace.correlation import request_id_context

METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "CONNECT", "TRACE"}
EVENTS = {
    "http_request_completed", "http_request_disconnected", "readiness_checked",
    "application_started", "application_stopped", "application_startup_failed",
    "application_shutdown_failed", "application_event",
}
ERROR_CATEGORIES = {
    "invalid_request", "authentication_required", "forbidden", "not_found",
    "method_not_allowed", "conflict", "payload_too_large", "unsupported_media_type",
    "validation_failed", "rate_limited", "internal_error", "dependency_unavailable",
    "storage_unavailable", "startup_failed", "shutdown_failed", "response_failed",
    "client_disconnected", "request_rejected",
}
LEVELS = {name: getattr(logging, name) for name in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")}


def safe_method(value) -> str:
    return value if isinstance(value, str) and value in METHODS else "OTHER"


def safe_uuid(value) -> str | None:
    if isinstance(value, str):
        try:
            identifier = UUID(value)
        except ValueError:
            pass
        else:
            if str(identifier) == value.lower():
                return str(identifier)
    return None


class SafeJSONFormatter(logging.Formatter):
    def __init__(self, route_names: Callable[[], set[str]]):
        super().__init__()
        self.route_names = route_names

    def safe_route(self, value) -> str:
        return value if isinstance(value, str) and value in self.route_names() else "unmatched"

    def safe_fields(self, fields: dict) -> dict:
        result = {"request_id": safe_uuid(fields.get("request_id"))}
        if "method" in fields:
            result["method"] = safe_method(fields["method"])
        if "route" in fields:
            result["route"] = self.safe_route(fields["route"])
        status = fields.get("status_code")
        if type(status) is int and 100 <= status <= 599:
            result["status_code"] = status
        duration = fields.get("duration_ms")
        if type(duration) in {int, float} and math.isfinite(duration) and duration >= 0:
            result["duration_ms"] = round(duration, 3)
        category = fields.get("error_category")
        if isinstance(category, str) and category in ERROR_CATEGORIES:
            result["error_category"] = category
        ready = fields.get("readiness")
        if isinstance(ready, str) and ready in {"ready", "unavailable"}:
            result["readiness"] = ready
        return result

    def format(self, record: logging.LogRecord) -> str:
        event = record.msg if isinstance(record.msg, str) and record.msg in EVENTS else "application_event"
        data = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "service": "identity-workspace",
            "level": logging.getLevelName(record.levelno) if record.levelno in LEVELS.values() else "INFO",
            "event": event,
            **self.safe_fields(record.__dict__),
        }
        # Deliberately ignore getMessage(), args, exc_info, stack_info and extras.
        return json.dumps(data, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


class SafeStreamHandler(logging.StreamHandler):
    def __init__(self, stream, on_failure: Callable[[], None]):
        super().__init__(stream)
        self.on_failure = on_failure

    def handleError(self, record):
        # Standard logging's fallback may print the original message/arguments.
        # Report sink failures through metrics instead of dumping a record.
        self.on_failure()


class ApplicationLogger:
    def __init__(
        self, level: str, route_names: Callable[[], set[str]], *,
        stream: TextIO | None = None, on_failure: Callable[[], None],
    ):
        self.minimum_level = LEVELS[level]
        self.formatter = SafeJSONFormatter(route_names)
        self.handler = SafeStreamHandler(stream, on_failure)
        self.handler.setFormatter(self.formatter)
        self.record_factory = logging.getLogger("identity_workspace.events")

    def event(self, level: int, event, **fields):
        if level not in LEVELS.values() or level < self.minimum_level:
            return
        event = event if isinstance(event, str) and event in EVENTS else "application_event"
        fields.setdefault("request_id", request_id_context.get())
        # Remove unsafe fields before constructing a LogRecord, not just at output.
        safe = self.formatter.safe_fields(fields)
        record = self.record_factory.makeRecord(
            "identity_workspace.events", level, __file__, 0, event, (), None, extra=safe,
        )
        # A private handler avoids modifying root/third-party logging or sharing
        # handlers/log levels between independent app instances.
        self.handler.handle(record)

    def close(self):
        self.handler.close()
