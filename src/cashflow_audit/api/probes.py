from __future__ import annotations

import httpx

from cashflow_audit.settings import Settings


async def probe_openai(base_url: str | None, api_key: str | None) -> bool | None:
    if not base_url:
        return None
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(base_url.rstrip("/") + "/models", headers=headers)
        return response.status_code < 500
    except Exception:
        return False


def llm_probe_from_settings(settings: Settings):
    async def _probe() -> bool | None:
        return await probe_openai(settings.llm_base_url, settings.llm_api_key)

    return _probe


def embed_probe_from_settings(settings: Settings):
    async def _probe() -> bool | None:
        return await probe_openai(settings.embedding_base_url, settings.embedding_api_key)

    return _probe
