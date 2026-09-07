from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from cashflow_audit.api.context import AppContext
from cashflow_audit.observability import log_event
from cashflow_audit.settings import Settings

_LOGGER = logging.getLogger("cashflow_audit.api.probes")


@dataclass(frozen=True)
class ProbeResult:
    configured: bool
    reachable: bool
    model_present: bool | None
    error: str | None
    latency_ms: int | None = None

    @property
    def ok(self) -> bool | None:
        if not self.configured:
            return None
        return self.reachable and self.model_present is not False


ProbeFn = Callable[..., ProbeResult | Awaitable[ProbeResult]]
RedisPingFn = Callable[..., Any]


def _safe_target(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    return f"{host}{parts.path}"


def _extract_ids(payload: object) -> list[str] | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    ids: list[str] = []
    for item in data:
        if isinstance(item, dict):
            mid = item.get("id")
            if isinstance(mid, str):
                ids.append(mid)
    return ids


def _id_matches(mid: str, model: str) -> bool:
    return mid == model or mid.rsplit("/", 1)[-1] == model


def _emit(
    result: ProbeResult,
    *,
    msg: str,
    port: str | None,
    model: str | None,
    http_status: int | None,
    reason: str | None,
) -> ProbeResult:
    if reason == "unparsed_models" or result.ok is False:
        level = logging.WARNING
    else:
        level = logging.INFO
    log_event(
        _LOGGER,
        level,
        "probe_models",
        msg,
        port=port,
        model=model,
        reachable=result.reachable,
        model_present=result.model_present,
        http_status=http_status,
        reason=reason,
    )
    return result


async def probe_models(
    base_url: str | None,
    api_key: str | None,
    model: str | None,
    *,
    timeout: float = 2.0,
    transport: httpx.BaseTransport | None = None,
    port: str | None = None,
) -> ProbeResult:
    if not base_url or not api_key:
        return _emit(
            ProbeResult(configured=False, reachable=False, model_present=None, error="unset"),
            msg="models probe unset",
            port=port,
            model=model,
            http_status=None,
            reason="unset",
        )

    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    msg = _safe_target(url)
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.get(url, headers=headers)
    except httpx.TimeoutException:
        return _emit(
            ProbeResult(configured=True, reachable=False, model_present=None, error="timeout"),
            msg=msg,
            port=port,
            model=model,
            http_status=None,
            reason="timeout",
        )
    except Exception:
        return _emit(
            ProbeResult(configured=True, reachable=False, model_present=None, error="connect"),
            msg=msg,
            port=port,
            model=model,
            http_status=None,
            reason="connect",
        )

    if response.status_code < 200 or response.status_code >= 300:
        error = f"http_{response.status_code}"
        return _emit(
            ProbeResult(configured=True, reachable=False, model_present=None, error=error),
            msg=msg,
            port=port,
            model=model,
            http_status=response.status_code,
            reason=error,
        )

    try:
        payload: object = response.json()
    except Exception:
        payload = None
    ids = _extract_ids(payload)
    if not ids:
        return _emit(
            ProbeResult(configured=True, reachable=True, model_present=None, error=None),
            msg=msg,
            port=port,
            model=model,
            http_status=response.status_code,
            reason="unparsed_models",
        )
    if model is not None and any(_id_matches(mid, model) for mid in ids):
        return _emit(
            ProbeResult(configured=True, reachable=True, model_present=True, error=None),
            msg=msg,
            port=port,
            model=model,
            http_status=response.status_code,
            reason=None,
        )
    return _emit(
        ProbeResult(configured=True, reachable=True, model_present=False, error="model_missing"),
        msg=msg,
        port=port,
        model=model,
        http_status=response.status_code,
        reason="model_missing",
    )


_UNSET_RESULT = ProbeResult(configured=False, reachable=False, model_present=None, error="unset")


def _emit_ping(
    result: ProbeResult,
    *,
    event: str,
    msg: str,
    port: str | None,
    model: str | None,
    http_status: int | None,
    reason: str | None,
    latency_ms: int | None,
) -> ProbeResult:
    level = logging.ERROR if result.ok is False else logging.INFO
    log_event(
        _LOGGER,
        level,
        event,
        msg,
        port=port,
        model=model,
        reachable=result.reachable,
        model_present=result.model_present,
        http_status=http_status,
        reason=reason,
        latency_ms=latency_ms,
    )
    return result


async def _post_ping(
    base_url: str | None,
    api_key: str | None,
    model: str | None,
    *,
    path: str,
    payload: dict[str, object],
    event: str,
    port: str,
    timeout: float,
    transport: httpx.BaseTransport | None,
) -> ProbeResult:
    if not base_url or not api_key:
        return _emit_ping(
            _UNSET_RESULT,
            event=event,
            msg="ping unset",
            port=port,
            model=model,
            http_status=None,
            reason="unset",
            latency_ms=None,
        )
    url = base_url.rstrip("/") + path
    headers = {"Authorization": f"Bearer {api_key}"}
    msg = _safe_target(url)
    started = time.perf_counter()
    latency_ms: int | None = None
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return _emit_ping(
            ProbeResult(
                configured=True,
                reachable=False,
                model_present=None,
                error="timeout",
                latency_ms=latency_ms,
            ),
            event=event,
            msg=msg,
            port=port,
            model=model,
            http_status=None,
            reason="timeout",
            latency_ms=latency_ms,
        )
    except Exception:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return _emit_ping(
            ProbeResult(
                configured=True,
                reachable=False,
                model_present=None,
                error="connect",
                latency_ms=latency_ms,
            ),
            event=event,
            msg=msg,
            port=port,
            model=model,
            http_status=None,
            reason="connect",
            latency_ms=latency_ms,
        )
    latency_ms = int((time.perf_counter() - started) * 1000)
    if response.status_code < 200 or response.status_code >= 300:
        error = f"http_{response.status_code}"
        return _emit_ping(
            ProbeResult(
                configured=True,
                reachable=False,
                model_present=None,
                error=error,
                latency_ms=latency_ms,
            ),
            event=event,
            msg=msg,
            port=port,
            model=model,
            http_status=response.status_code,
            reason=error,
            latency_ms=latency_ms,
        )
    return _emit_ping(
        ProbeResult(
            configured=True,
            reachable=True,
            model_present=True,
            error=None,
            latency_ms=latency_ms,
        ),
        event=event,
        msg=msg,
        port=port,
        model=model,
        http_status=response.status_code,
        reason=None,
        latency_ms=latency_ms,
    )


async def ping_chat(
    base_url: str | None,
    api_key: str | None,
    model: str | None,
    *,
    timeout: float = 10.0,
    transport: httpx.BaseTransport | None = None,
) -> ProbeResult:
    return await _post_ping(
        base_url,
        api_key,
        model,
        path="/chat/completions",
        payload={
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "stream": False,
        },
        event="ping_chat",
        port="llm",
        timeout=timeout,
        transport=transport,
    )


async def ping_embed(
    base_url: str | None,
    api_key: str | None,
    model: str | None,
    *,
    timeout: float = 10.0,
    transport: httpx.BaseTransport | None = None,
) -> ProbeResult:
    return await _post_ping(
        base_url,
        api_key,
        model,
        path="/embeddings",
        payload={"model": model, "input": ["ping"]},
        event="ping_embed",
        port="embed",
        timeout=timeout,
        transport=transport,
    )


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _emit_redis(result: ProbeResult) -> ProbeResult:
    level = logging.ERROR if result.ok is False else logging.INFO
    log_event(
        _LOGGER,
        level,
        "ping_redis",
        "redis ping",
        port="redis",
        reachable=result.reachable,
        reason=result.error,
    )
    return result


def _redis_result(raw: object) -> ProbeResult:
    if raw is None:
        result = _UNSET_RESULT
    elif isinstance(raw, ProbeResult):
        result = raw
    elif raw:
        result = ProbeResult(configured=True, reachable=True, model_present=True, error=None)
    else:
        result = ProbeResult(configured=True, reachable=False, model_present=None, error="connect")
    return _emit_redis(result)


async def _default_redis_ping(url: str) -> bool:
    import redis.asyncio as redis

    client = redis.from_url(url)
    try:
        return bool(await client.ping())
    except Exception:
        return False
    finally:
        await client.aclose()


async def run_connectivity_checks(
    settings: Settings,
    *,
    probe_fn: ProbeFn | None = None,
    ping_chat_fn: ProbeFn | None = None,
    ping_embed_fn: ProbeFn | None = None,
    redis_ping_fn: RedisPingFn | None = None,
) -> list[tuple[str, ProbeResult]]:
    probe = probe_fn if probe_fn is not None else probe_models
    chat_ping = ping_chat_fn if ping_chat_fn is not None else ping_chat
    embed_ping = ping_embed_fn if ping_embed_fn is not None else ping_embed
    results: list[tuple[str, ProbeResult]] = []

    if settings.llm_configured():
        results.append(
            (
                "llm_probe",
                await _maybe_await(
                    probe(
                        settings.llm_base_url,
                        settings.llm_api_key,
                        settings.llm_model,
                        port="llm",
                    )
                ),
            )
        )
        results.append(
            (
                "llm_ping",
                await _maybe_await(
                    chat_ping(settings.llm_base_url, settings.llm_api_key, settings.llm_model)
                ),
            )
        )
    else:
        results.append(("llm_ping", _UNSET_RESULT))

    if settings.embed_configured():
        results.append(
            (
                "embed_probe",
                await _maybe_await(
                    probe(
                        settings.embedding_base_url,
                        settings.embedding_api_key,
                        settings.embedding_model,
                        port="embed",
                    )
                ),
            )
        )
        results.append(
            (
                "embed_ping",
                await _maybe_await(
                    embed_ping(
                        settings.embedding_base_url,
                        settings.embedding_api_key,
                        settings.embedding_model,
                    )
                ),
            )
        )
    else:
        results.append(("embed_ping", _UNSET_RESULT))

    if redis_ping_fn is not None:
        raw = await _maybe_await(redis_ping_fn())
    elif settings.redis_url:
        raw = await _default_redis_ping(settings.redis_url)
    else:
        raw = None
    results.append(("redis", _redis_result(raw)))
    return results


async def log_startup_ports(ctx: AppContext) -> None:
    settings = ctx.settings
    if settings is None:
        return
    try:
        log_event(
            _LOGGER,
            logging.INFO,
            "serve_start",
            (
                f"configured llm={settings.llm_configured()} "
                f"embed={settings.embed_configured()} redis={bool(settings.redis_url)}"
            ),
        )
        await run_connectivity_checks(settings, redis_ping_fn=ctx.bus.ping)
    except Exception:
        log_event(_LOGGER, logging.ERROR, "serve_start", "startup port checks failed")


def llm_probe_from_settings(settings: Settings):
    async def _probe() -> bool | None:
        if not settings.llm_configured():
            return None
        result = await probe_models(
            settings.llm_base_url,
            settings.llm_api_key,
            settings.llm_model,
            port="llm",
        )
        return result.ok

    return _probe


def embed_probe_from_settings(settings: Settings):
    async def _probe() -> bool | None:
        if not settings.embed_configured():
            return None
        result = await probe_models(
            settings.embedding_base_url,
            settings.embedding_api_key,
            settings.embedding_model,
            port="embed",
        )
        return result.ok

    return _probe
