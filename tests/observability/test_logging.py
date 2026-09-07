from __future__ import annotations

import json
import logging
from io import StringIO

from cashflow_audit.observability import (
    FORBIDDEN_LOG_KEYS,
    audit_id_var,
    configure_logging,
    log_event,
    stage_var,
)


def test_json_line_has_event_and_audit_id() -> None:
    stream = StringIO()
    configure_logging(level="INFO", json_output=True, stream=stream)
    token = audit_id_var.set("abc123")
    try:
        log_event(
            logging.getLogger("cashflow_audit.test"),
            logging.INFO,
            "stage_start",
            "parse begin",
            stage="parse",
        )
    finally:
        audit_id_var.reset(token)
    line = stream.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "stage_start"
    assert payload["audit_id"] == "abc123"
    assert payload["stage"] == "parse"
    assert payload["level"] == "INFO"
    assert "ts" in payload


def test_forbidden_extras_are_dropped() -> None:
    stream = StringIO()
    configure_logging(level="INFO", json_output=True, stream=stream)
    log_event(
        logging.getLogger("cashflow_audit.test"),
        logging.INFO,
        "stage_start",
        "x",
        source_filename="ClientCashflow.xlsx",
        actor_id="analyst-42",
        formula_raw="=A1+B1",
        cached_value="1000000",
        api_key="sk-secret",
        duration_ms=12,
    )
    payload = json.loads(stream.getvalue().strip().splitlines()[-1])
    for key in FORBIDDEN_LOG_KEYS:
        assert key not in payload
    assert payload["duration_ms"] == 12
    assert "sk-secret" not in stream.getvalue()
    assert "ClientCashflow" not in stream.getvalue()


def test_context_stage_included() -> None:
    stream = StringIO()
    configure_logging(level="INFO", json_output=True, stream=stream)
    token = stage_var.set("mapping")
    try:
        log_event(
            logging.getLogger("cashflow_audit.test"),
            logging.INFO,
            "port_fallback",
            "embed down",
        )
    finally:
        stage_var.reset(token)
    payload = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert payload["stage"] == "mapping"
    assert payload["event"] == "port_fallback"
