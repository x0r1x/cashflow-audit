from __future__ import annotations

import os

import httpx


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


def llm_probe_from_env():
    async def _probe() -> bool | None:
        return await probe_openai(os.environ.get("LLM_BASE_URL"), os.environ.get("LLM_API_KEY"))

    return _probe


def embed_probe_from_env():
    async def _probe() -> bool | None:
        return await probe_openai(
            os.environ.get("EMBEDDING_BASE_URL"), os.environ.get("EMBEDDING_API_KEY")
        )

    return _probe
