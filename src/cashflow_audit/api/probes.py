from __future__ import annotations

import inspect
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from cashflow_audit.adapters.routes import (
    CHAT_SUFFIX,
    EMBED_SUFFIX,
    join_route,
    openai_path,
    wrap_async_transport,
)
from cashflow_audit.adapters.tls import httpx_verify
from cashflow_audit.api.context import AppContext
from cashflow_audit.errors import PortError
from cashflow_audit.observability import log_event
from cashflow_audit.settings import Settings

_LOGGER = logging.getLogger("cashflow_audit.api.probes")
_LAST_PROBE: dict[str, tuple[object, ...]] = {}
_PLACEHOLDER_KEY = "not-needed"


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


_EMBED_ID_PREFIX = "text-embedding-"


def _id_matches(mid: str, model: str) -> bool:
    if mid == model or mid.rsplit("/", 1)[-1] == model:
        return True
    return mid == _EMBED_ID_PREFIX + model or model == _EMBED_ID_PREFIX + mid


def _port_error_reason(exc: PortError) -> str:
    msg = str(exc).lower()
    if "tls" in msg:
        return "tls"
    if "path" in msg:
        return "path"
    return "connect"


@asynccontextmanager
async def _openai_client(
    *,
    base_url: str,
    api_key: str | None,
    timeout: float,
    transport: httpx.BaseTransport | None,
    ca_file: Path | None = None,
    sdk_suffix: str = "",
    dest_path: str = "",
) -> AsyncIterator[Any]:
    from openai import AsyncOpenAI

    dest = dest_path or sdk_suffix
    try:
        needs_rewrite = bool(sdk_suffix) and openai_path(dest, sdk_suffix) != sdk_suffix
        if needs_rewrite:
            wrapped = wrap_async_transport(
                transport,
                base_url=base_url,
                ca_file=ca_file,
                sdk_suffix=sdk_suffix,
                dest_path=dest,
            )
            http_kwargs: dict[str, Any] = {"timeout": timeout, "transport": wrapped}
            if transport is not None:
                http_kwargs["verify"] = True
            http = httpx.AsyncClient(**http_kwargs)
        else:
            verify = True if transport is not None else httpx_verify(ca_file)
            http = httpx.AsyncClient(timeout=timeout, transport=transport, verify=verify)
    except PortError:
        raise
    client = AsyncOpenAI(
        base_url=base_url,
        api_key=api_key or _PLACEHOLDER_KEY,
        http_client=http,
    )
    try:
        yield client
    finally:
        await client.close()


def _openai_fail(exc: BaseException) -> tuple[str, int | None]:
    from openai import APIConnectionError, APIStatusError, APITimeoutError

    if isinstance(exc, (APITimeoutError, httpx.TimeoutException)):
        return "timeout", None
    if isinstance(exc, APIStatusError):
        return f"http_{exc.status_code}", exc.status_code
    if isinstance(exc, APIConnectionError):
        return "connect", None
    return "connect", None


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
    key = port or ""
    snapshot = (result.ok, result.reachable, result.model_present, result.error, reason)
    prev = _LAST_PROBE.get(key)
    _LAST_PROBE[key] = snapshot
    if prev == snapshot:
        return result
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
    ca_file: Path | None = None,
) -> ProbeResult:
    if not base_url:
        return _emit(
            ProbeResult(configured=False, reachable=False, model_present=None, error="unset"),
            msg="models probe unset",
            port=port,
            model=model,
            http_status=None,
            reason="unset",
        )

    url = base_url.rstrip("/") + "/models"
    msg = _safe_target(url)
    try:
        async with _openai_client(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            transport=transport,
            ca_file=ca_file,
        ) as client:
            page = await client.models.list()
    except PortError as exc:
        reason = _port_error_reason(exc)
        return _emit(
            ProbeResult(configured=True, reachable=False, model_present=None, error=reason),
            msg=msg,
            port=port,
            model=model,
            http_status=None,
            reason=reason,
        )
    except Exception as exc:
        from openai import APIResponseValidationError

        if isinstance(exc, APIResponseValidationError):
            return _emit(
                ProbeResult(configured=True, reachable=True, model_present=None, error=None),
                msg=msg,
                port=port,
                model=model,
                http_status=getattr(exc, "status_code", 200) or 200,
                reason="unparsed_models",
            )
        error, status = _openai_fail(exc)
        return _emit(
            ProbeResult(configured=True, reachable=False, model_present=None, error=error),
            msg=msg,
            port=port,
            model=model,
            http_status=status,
            reason=error,
        )

    ids = [item.id for item in (page.data or []) if getattr(item, "id", None)]
    if not ids:
        return _emit(
            ProbeResult(configured=True, reachable=True, model_present=None, error=None),
            msg=msg,
            port=port,
            model=model,
            http_status=200,
            reason="unparsed_models",
        )
    if model is not None and any(_id_matches(mid, model) for mid in ids):
        return _emit(
            ProbeResult(configured=True, reachable=True, model_present=True, error=None),
            msg=msg,
            port=port,
            model=model,
            http_status=200,
            reason=None,
        )
    return _emit(
        ProbeResult(configured=True, reachable=True, model_present=False, error="model_missing"),
        msg=msg,
        port=port,
        model=model,
        http_status=200,
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


async def _sdk_ping(
    base_url: str | None,
    api_key: str | None,
    model: str | None,
    *,
    path: str,
    event: str,
    port: str,
    timeout: float,
    transport: httpx.BaseTransport | None,
    call: Callable[[Any], Awaitable[Any]],
    ca_file: Path | None = None,
    sdk_suffix: str = "",
    dest_path: str = "",
) -> ProbeResult:
    if not base_url:
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
    dest = dest_path or path
    url = join_route(base_url, dest)
    msg = _safe_target(url)
    started = time.perf_counter()
    latency_ms: int | None = None
    try:
        async with _openai_client(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            transport=transport,
            ca_file=ca_file,
            sdk_suffix=sdk_suffix,
            dest_path=dest,
        ) as client:
            await call(client)
    except PortError as exc:
        reason = _port_error_reason(exc)
        return _emit_ping(
            ProbeResult(
                configured=True,
                reachable=False,
                model_present=None,
                error=reason,
            ),
            event=event,
            msg=msg,
            port=port,
            model=model,
            http_status=None,
            reason=reason,
            latency_ms=None,
        )
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        error, status = _openai_fail(exc)
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
            http_status=status,
            reason=error,
            latency_ms=latency_ms,
        )
    latency_ms = int((time.perf_counter() - started) * 1000)
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
        http_status=200,
        reason=None,
        latency_ms=latency_ms,
    )


async def ping_chat(
    base_url: str | None,
    api_key: str | None,
    model: str | None,
    *,
    timeout: float = 30.0,
    transport: httpx.BaseTransport | None = None,
    ca_file: Path | None = None,
    chat_path: str = CHAT_SUFFIX,
) -> ProbeResult:
    async def _call(client: Any) -> Any:
        return await client.chat.completions.create(
            model=model or "",
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            stream=False,
            tool_choice="none",
        )

    try:
        dest = openai_path(chat_path, CHAT_SUFFIX)
    except PortError as exc:
        reason = _port_error_reason(exc)
        return _emit_ping(
            ProbeResult(configured=True, reachable=False, model_present=None, error=reason),
            event="ping_chat",
            msg="ping path",
            port="llm",
            model=model,
            http_status=None,
            reason=reason,
            latency_ms=None,
        )
    return await _sdk_ping(
        base_url,
        api_key,
        model,
        path=dest,
        event="ping_chat",
        port="llm",
        timeout=timeout,
        transport=transport,
        call=_call,
        ca_file=ca_file,
        sdk_suffix=CHAT_SUFFIX,
        dest_path=dest,
    )


async def ping_embed(
    base_url: str | None,
    api_key: str | None,
    model: str | None,
    *,
    timeout: float = 10.0,
    transport: httpx.BaseTransport | None = None,
    ca_file: Path | None = None,
    embed_path: str = EMBED_SUFFIX,
) -> ProbeResult:
    async def _call(client: Any) -> Any:
        return await client.embeddings.create(model=model or "", input=["ping"])

    try:
        dest = openai_path(embed_path, EMBED_SUFFIX)
    except PortError as exc:
        reason = _port_error_reason(exc)
        return _emit_ping(
            ProbeResult(configured=True, reachable=False, model_present=None, error=reason),
            event="ping_embed",
            msg="ping path",
            port="embed",
            model=model,
            http_status=None,
            reason=reason,
            latency_ms=None,
        )
    return await _sdk_ping(
        base_url,
        api_key,
        model,
        path=dest,
        event="ping_embed",
        port="embed",
        timeout=timeout,
        transport=transport,
        call=_call,
        ca_file=ca_file,
        sdk_suffix=EMBED_SUFFIX,
        dest_path=dest,
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
                        ca_file=settings.llm_tls_ca_file,
                    )
                ),
            )
        )
        results.append(
            (
                "llm_ping",
                await _maybe_await(
                    chat_ping(
                        settings.llm_base_url,
                        settings.llm_api_key,
                        settings.llm_model,
                        ca_file=settings.llm_tls_ca_file,
                        chat_path=settings.llm_chat_path,
                    )
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
                        ca_file=settings.embedding_tls_ca_file,
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
                        ca_file=settings.embedding_tls_ca_file,
                        embed_path=settings.embedding_path,
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
            ca_file=settings.llm_tls_ca_file,
        )
        return result.ok

    _probe.emits_probe_log = True  # type: ignore[attr-defined]
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
            ca_file=settings.embedding_tls_ca_file,
        )
        return result.ok

    _probe.emits_probe_log = True  # type: ignore[attr-defined]
    return _probe
