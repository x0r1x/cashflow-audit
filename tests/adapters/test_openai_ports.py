from __future__ import annotations

import json
from io import StringIO

from pydantic import BaseModel

from cashflow_audit.adapters.openai_chat import OpenAIChat
from cashflow_audit.adapters.openai_embed import OpenAIEmbed
from cashflow_audit.errors import PortError
from cashflow_audit.observability import configure_logging


class _Schema(BaseModel):
    x: str


def _boom(status_code: int = 500) -> Exception:
    exc = Exception("upstream echoed secret-label")
    exc.status_code = status_code  # type: ignore[attr-defined]
    return exc


def test_chat_complete_json_uses_sdk_parse() -> None:
    seen: dict[str, object] = {}

    class FakeCompletions:
        def parse(self, **kwargs):
            seen.update(kwargs)

            class _Msg:
                parsed = _Schema(x="ok")
                content = None

            class _Choice:
                message = _Msg()

            class _Resp:
                choices = [_Choice()]

            return _Resp()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    port = OpenAIChat.__new__(OpenAIChat)
    port._client = FakeClient()
    port.model = "gemma"
    got = port.complete_json(_Schema, [{"role": "user", "content": "hi"}])
    assert got == _Schema(x="ok")
    assert seen["tool_choice"] == "none"
    assert seen["model"] == "gemma"
    assert seen["response_format"] is _Schema


def test_chat_failure_logs_status_not_prompt() -> None:
    stream = StringIO()
    configure_logging(level="INFO", json_output=True, stream=stream)

    class FakeCompletions:
        def parse(self, **kwargs):
            raise _boom()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    port = OpenAIChat.__new__(OpenAIChat)
    port._client = FakeClient()
    port.model = "qwen3.6-27b-fp8"
    try:
        port.complete_json(_Schema, [{"role": "user", "content": "secret-label"}])
    except PortError as exc:
        assert exc.port == "chat"
        assert exc.status_code == 500
    else:
        raise AssertionError("expected PortError")
    dumped = stream.getvalue()
    assert "secret-label" not in dumped
    payload = json.loads(dumped.strip().splitlines()[-1])
    assert payload["event"] == "port_error"
    assert payload["http_status"] == 500
    assert payload["port"] == "chat"
    assert payload["model"] == "qwen3.6-27b-fp8"
    assert payload["exc_type"] == "Exception"
    assert "latency_ms" in payload


def test_embed_failure_logs_status_not_prompt() -> None:
    stream = StringIO()
    configure_logging(level="INFO", json_output=True, stream=stream)

    class FakeEmbeddings:
        def create(self, **kwargs):
            raise _boom()

    class FakeClient:
        embeddings = FakeEmbeddings()

    port = OpenAIEmbed.__new__(OpenAIEmbed)
    port._client = FakeClient()
    port.model = "qwen3-embedding-8b"
    try:
        port.embed(["secret-label"])
    except PortError as exc:
        assert exc.port == "embed"
        assert exc.status_code == 500
    else:
        raise AssertionError("expected PortError")
    dumped = stream.getvalue()
    assert "secret-label" not in dumped
    payload = json.loads(dumped.strip().splitlines()[-1])
    assert payload["event"] == "port_error"
    assert payload["http_status"] == 500
    assert payload["port"] == "embed"
    assert payload["model"] == "qwen3-embedding-8b"
    assert payload["exc_type"] == "Exception"
    assert "latency_ms" in payload


def test_port_error_message_only_still_works() -> None:
    exc = PortError("embed down")
    assert str(exc) == "embed down"
    assert exc.port == ""
    assert exc.status_code is None
