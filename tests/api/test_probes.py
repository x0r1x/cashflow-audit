from __future__ import annotations

import asyncio
import json
import logging

import httpx

from cashflow_audit.api.probes import (
    ProbeResult,
    embed_probe_from_settings,
    llm_probe_from_settings,
    ping_chat,
    ping_embed,
    probe_models,
    run_connectivity_checks,
)
from cashflow_audit.settings import Settings


def _transport(handler):
    return httpx.MockTransport(handler)


def test_unset_without_url() -> None:
    async def _go() -> None:
        result = await probe_models(None, "k", "qwen")
        assert result.configured is False
        assert result.ok is None
        assert result.error == "unset"

    asyncio.run(_go())


def test_unset_without_api_key() -> None:
    async def _go() -> None:
        result = await probe_models("http://llm/v1", None, "qwen")
        assert result.configured is False
        assert result.ok is None
        assert result.error == "unset"

    asyncio.run(_go())


def test_2xx_with_model_id_is_ok() -> None:
    async def _go() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/models")
            assert request.headers.get("Authorization") == "Bearer secret"
            return httpx.Response(200, json={"data": [{"id": "qwen3.6-27b-fp8"}]})

        result = await probe_models(
            "http://llm/v1",
            "secret",
            "qwen3.6-27b-fp8",
            transport=_transport(handler),
        )
        assert result.ok is True
        assert result.model_present is True

    asyncio.run(_go())


def test_2xx_suffix_id_is_ok() -> None:
    async def _go() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"id": "org/qwen3.6-27b-fp8"}]})

        result = await probe_models(
            "http://llm/v1",
            "secret",
            "qwen3.6-27b-fp8",
            transport=_transport(handler),
        )
        assert result.ok is True
        assert result.model_present is True

    asyncio.run(_go())


def test_2xx_missing_model_is_down() -> None:
    async def _go() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"id": "other-model"}]})

        result = await probe_models(
            "http://llm/v1",
            "secret",
            "qwen3.6-27b-fp8",
            transport=_transport(handler),
        )
        assert result.ok is False
        assert result.error == "model_missing"

    asyncio.run(_go())


def test_401_is_down_not_up() -> None:
    async def _go() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "nope"})

        result = await probe_models(
            "http://llm/v1",
            "secret",
            "qwen",
            transport=_transport(handler),
        )
        assert result.ok is False
        assert result.error == "http_401"

    asyncio.run(_go())


def test_timeout_is_down() -> None:
    async def _go() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("slow")

        result = await probe_models(
            "http://llm/v1",
            "secret",
            "qwen",
            transport=_transport(handler),
        )
        assert result.ok is False
        assert result.error in {"timeout", "connect"}

    asyncio.run(_go())


def test_2xx_empty_or_unparsed_is_ok(caplog) -> None:
    async def _go() -> None:
        def empty(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": []})

        def unparsed(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"not": "models"})

        empty_result = await probe_models(
            "http://llm/v1",
            "secret",
            "qwen",
            transport=_transport(empty),
        )
        unparsed_result = await probe_models(
            "http://llm/v1",
            "secret",
            "qwen",
            transport=_transport(unparsed),
        )
        assert empty_result.ok is True
        assert empty_result.model_present is None
        assert unparsed_result.ok is True
        assert unparsed_result.model_present is None

    with caplog.at_level(logging.WARNING, logger="cashflow_audit.api.probes"):
        asyncio.run(_go())
    reasons = [getattr(record, "reason", None) for record in caplog.records]
    assert reasons.count("unparsed_models") >= 2
    events = [getattr(record, "event", None) for record in caplog.records]
    assert "probe_models" in events


def test_probe_info_only_when_result_changes(caplog) -> None:
    async def _go() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"id": "qwen"}]})

        transport = _transport(handler)
        await probe_models(
            "http://llm/v1",
            "secret",
            "qwen",
            transport=transport,
            port="probe-change",
        )
        caplog.clear()
        await probe_models(
            "http://llm/v1",
            "secret",
            "qwen",
            transport=transport,
            port="probe-change",
        )

    caplog.set_level(logging.INFO, logger="cashflow_audit.api.probes")
    asyncio.run(_go())
    assert not any(
        getattr(record, "event", None) == "probe_models" and record.levelno == logging.INFO
        for record in caplog.records
    )


def test_probe_does_not_log_key_or_userinfo(caplog) -> None:
    async def _go() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"id": "qwen"}]})

        await probe_models(
            "http://user:pass@llm/v1",
            "sk-secret",
            "qwen",
            transport=_transport(handler),
        )

    with caplog.at_level(logging.DEBUG, logger="cashflow_audit.api.probes"):
        asyncio.run(_go())
    text = caplog.text
    assert "sk-secret" not in text
    assert "user:pass" not in text
    assert "pass@" not in text


def test_llm_probe_unset_is_none(monkeypatch) -> None:
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    settings = Settings(_env_file=None)

    async def _go() -> None:
        assert await llm_probe_from_settings(settings)() is None

    asyncio.run(_go())


def test_embed_probe_unset_is_none(monkeypatch) -> None:
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://emb")
    monkeypatch.setenv("EMBEDDING_API_KEY", "emb-key")
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    settings = Settings(_env_file=None)

    async def _go() -> None:
        assert await embed_probe_from_settings(settings)() is None

    asyncio.run(_go())


def test_ping_chat_2xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        body = json.loads(request.content)
        assert body["messages"][0]["content"] == "ping"
        assert body["max_tokens"] == 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    result = asyncio.run(ping_chat("http://llm/v1", "k", "qwen", transport=_transport(handler)))
    assert result.ok is True


def test_ping_embed_503() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={})

    result = asyncio.run(ping_embed("http://emb/v1", "k", "e5", transport=_transport(handler)))
    assert result.ok is False
    assert result.error == "http_503"


def test_ping_embed_2xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/embeddings")
        body = json.loads(request.content)
        assert body["input"] == ["ping"]
        assert body["model"] == "e5"
        return httpx.Response(200, json={"data": [{"embedding": [0.1]}]})

    result = asyncio.run(ping_embed("http://emb/v1", "k", "e5", transport=_transport(handler)))
    assert result.ok is True


def test_ping_chat_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow")

    result = asyncio.run(ping_chat("http://llm/v1", "k", "qwen", transport=_transport(handler)))
    assert result.ok is False
    assert result.error in {"timeout", "connect"}


def test_ping_chat_unset() -> None:
    result = asyncio.run(ping_chat(None, "k", "qwen"))
    assert result.ok is None
    assert result.error == "unset"


def _ok(*_a, **_k) -> ProbeResult:
    return ProbeResult(configured=True, reachable=True, model_present=True, error=None)


def _down(*_a, **_k) -> ProbeResult:
    return ProbeResult(configured=True, reachable=False, model_present=None, error="http_503")


def _unset(*_a, **_k) -> ProbeResult:
    return ProbeResult(configured=False, reachable=False, model_present=None, error="unset")


def test_run_connectivity_fails_when_llm_down(monkeypatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "http://llm/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    settings = Settings(_env_file=None)
    results = asyncio.run(
        run_connectivity_checks(
            settings,
            probe_fn=_ok,
            ping_chat_fn=_down,
            ping_embed_fn=_unset,
            redis_ping_fn=lambda: None,
        )
    )
    assert any(name == "llm_ping" and r.ok is False for name, r in results)


def test_run_connectivity_unset_does_not_fail(monkeypatch) -> None:
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    settings = Settings(_env_file=None)
    results = asyncio.run(
        run_connectivity_checks(
            settings,
            probe_fn=_ok,
            ping_chat_fn=_down,
            ping_embed_fn=_down,
            redis_ping_fn=lambda: None,
        )
    )
    assert results
    assert all(r.ok is not False for _, r in results)


def test_run_connectivity_logs_redis_down(monkeypatch, caplog) -> None:
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    settings = Settings(_env_file=None)
    with caplog.at_level(logging.ERROR, logger="cashflow_audit.api.probes"):
        asyncio.run(
            run_connectivity_checks(
                settings,
                probe_fn=_ok,
                ping_chat_fn=_unset,
                ping_embed_fn=_unset,
                redis_ping_fn=lambda: False,
            )
        )
    redis_records = [
        record for record in caplog.records if getattr(record, "event", None) == "ping_redis"
    ]
    assert redis_records
    assert redis_records[-1].levelno == logging.ERROR
    assert getattr(redis_records[-1], "reachable", None) is False
    assert "redis://" not in caplog.text


def test_ping_does_not_log_key(caplog) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    with caplog.at_level(logging.DEBUG, logger="cashflow_audit.api.probes"):
        asyncio.run(
            ping_chat("http://user:pass@llm/v1", "sk-secret", "qwen", transport=_transport(handler))
        )
    text = caplog.text
    assert "sk-secret" not in text
    assert "user:pass" not in text
