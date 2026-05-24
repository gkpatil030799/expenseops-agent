from __future__ import annotations

import io
import json
import logging

from app.config import Settings
from app.logging_config import (
    JsonStructuredFormatter,
    LocalStructuredFormatter,
    configure_logging,
    log_event,
    reset_trace_id,
    safe_preview,
    set_trace_id,
)


def test_local_structured_formatter_includes_event_trace_and_metadata():
    record = logging.LogRecord("app.test", logging.INFO, __file__, 10, "msg", (), None, "_func")
    record.event = "ai_entity_resolution_success"
    record.trace_id = "trace-123"
    record.log_metadata = {"tx_id": 123, "resolved_count": 2}

    line = LocalStructuredFormatter().format(record)

    assert "event=ai_entity_resolution_success" in line
    assert "trace_id=trace-123" in line
    assert "tx_id=123" in line
    assert "resolved_count=2" in line


def test_json_structured_formatter_is_railway_friendly():
    record = logging.LogRecord("app.test", logging.WARNING, __file__, 10, "msg", (), None, "_func")
    record.event = "plaid_webhook_verification_failed"
    record.trace_id = "trace-123"
    record.log_metadata = {"reason": "plaid_verification_failed", "response_status": 401}

    payload = json.loads(JsonStructuredFormatter().format(record))

    assert payload["event"] == "plaid_webhook_verification_failed"
    assert payload["trace_id"] == "trace-123"
    assert payload["reason"] == "plaid_verification_failed"
    assert payload["response_status"] == 401


def test_log_event_redacts_sensitive_keys_but_not_safe_response_status():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(LocalStructuredFormatter())
    logger = logging.getLogger("tests.logging.redaction")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    token = set_trace_id("trace-redact")
    try:
        log_event(
            logger,
            "telegram_ai_started",
            plaid_access_token="access-sandbox-secret",
            openai_api_key="sk-secret",
            raw_response="provider said secret",
            response_status=400,
            status_code=400,
        )
    finally:
        reset_trace_id(token)

    line = stream.getvalue()
    assert "access-sandbox-secret" not in line
    assert "sk-secret" not in line
    assert "provider said secret" not in line
    assert "[REDACTED]" in line
    assert "response_status=400" in line
    assert "status_code=400" in line


def test_safe_preview_collapses_whitespace_and_caps_length():
    preview = safe_preview(" hello\n\nworld " + "x" * 400, max_length=20)

    assert "\n" not in preview
    assert preview.startswith("hello world")
    assert preview.endswith("…")
    assert len(preview) == 20


def test_configure_logging_uses_json_in_production():
    configure_logging(Settings(environment="production"))

    assert isinstance(logging.getLogger().handlers[0].formatter, JsonStructuredFormatter)
    assert logging.getLogger().level == logging.INFO


def test_configure_logging_uses_readable_logs_locally():
    configure_logging(Settings(environment="local"))

    assert isinstance(logging.getLogger().handlers[0].formatter, LocalStructuredFormatter)
    assert logging.getLogger().level == logging.DEBUG
