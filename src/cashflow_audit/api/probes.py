from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from cashflow_audit.observability import log_event
from cashflow_audit.settings import Settings

_LOGGER = logging.getLogger("cashflow_audit.api.probes")


@dataclass(frozen=True)
class ProbeResult:
    configured: bool
    reachable: bool
    model_present: bool | None
    error: str | None

    @property
    def ok(self) -> bool | None:
        if not self.configured:
            return None
        return self.reachable and self.model_present is not False


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
