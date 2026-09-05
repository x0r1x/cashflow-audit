from __future__ import annotations

import os

from cashflow_audit.errors import PortError


class OpenAIEmbed:
    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        from openai import OpenAI

        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            response = self._client.embeddings.create(model=self.model, input=texts)
            return [list(item.embedding) for item in response.data]
        except Exception as exc:
            raise PortError("embed failed") from exc


def embed_from_env() -> OpenAIEmbed | None:
    base = os.environ.get("EMBEDDING_BASE_URL")
    key = os.environ.get("EMBEDDING_API_KEY")
    model = os.environ.get("EMBEDDING_MODEL")
    if not base or not key or not model:
        return None
    return OpenAIEmbed(base_url=base, api_key=key, model=model)
