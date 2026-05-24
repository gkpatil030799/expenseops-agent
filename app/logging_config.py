from __future__ import annotations

import json
import logging
import re
import secrets
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

from app.config import Settings, get_settings

_trace_id: ContextVar[str | None] = ContextVar("expenseops_trace_id", default=None)

_SENSITIVE_KEY_PATTERN = re.compile(
    r"(token|secret|password|api[_-]?key|authorization|auth[_-]?header|"
    r"access[_-]?token|raw[_-]?prompt|raw[_-]?response|provider[_-]?response|"
    r"raw[_-]?payload|plaid[_-]?payload)",
    re.IGNORECASE,
)
_MAX_VALUE_LENGTH = 240


def new_trace_id() -> str:
    return secrets.token_hex(8)


def set_trace_id(trace_id: str | None = None) -> Token[str | None]:
    return _trace_id.set(trace_id or new_trace_id())


def reset_trace_id(token: Token[str | None]) -> None:
    _trace_id.reset(token)


def get_trace_id() -> str | None:
    return _trace_id.get()


def safe_preview(value: Any, *, max_length: int = _MAX_VALUE_LENGTH) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 1]}…"


def _redact_value(key: str, value: Any) -> Any:
    if _SENSITIVE_KEY_PATTERN.search(key):
        return "[REDACTED]"
    if isinstance(value, str):
        return safe_preview(value)
    if isinstance(value, dict):
        return {str(k): _redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(key, item) for item in value[:20]]
    return value


def redact_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {key: _redact_value(key, value) for key, value in metadata.items()}


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **metadata: Any,
) -> None:
    logger.log(
        level,
        event,
        extra={
            "event": event,
            "trace_id": get_trace_id(),
            "log_metadata": redact_metadata(metadata),
        },
        stacklevel=2,
    )


class LocalStructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, UTC).isoformat().replace("+00:00", "Z")
        event = getattr(record, "event", None)
        trace_id = getattr(record, "trace_id", None)
        metadata = getattr(record, "log_metadata", None) or {}
        parts = [
            timestamp,
            record.levelname,
            f"module={record.module}",
            f"function={record.funcName}",
        ]
        if event:
            parts.insert(2, f"event={event}")
        if trace_id:
            parts.append(f"trace_id={trace_id}")
        for key, value in metadata.items():
            parts.append(f"{key}={_format_value(value)}")
        if not event:
            parts.append(f"message={_format_value(safe_preview(record.getMessage()))}")
        return " ".join(parts)


class JsonStructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", None)
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "event": event or "log_message",
            "module": record.module,
            "function": record.funcName,
            "trace_id": getattr(record, "trace_id", None),
        }
        if event:
            payload.update(getattr(record, "log_metadata", None) or {})
        else:
            payload["message"] = safe_preview(record.getMessage())
        return json.dumps(payload, separators=(",", ":"), default=str)


def configure_logging(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG if settings.environment == "local" else logging.INFO)

    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG if settings.environment == "local" else logging.INFO)
    handler.setFormatter(
        JsonStructuredFormatter()
        if settings.environment == "production"
        else LocalStructuredFormatter()
    )
    root.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    text = str(value)
    return json.dumps(text) if re.search(r"\s", text) else text
