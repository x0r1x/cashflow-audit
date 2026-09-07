from __future__ import annotations

import asyncio
import logging

import httpx

from cashflow_audit.api.probes import (
    embed_probe_from_settings,
    llm_probe_from_settings,
    probe_models,
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
