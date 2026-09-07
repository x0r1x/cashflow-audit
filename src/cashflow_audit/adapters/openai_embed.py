from __future__ import annotations

import logging
import time

from cashflow_audit.errors import PortError
from cashflow_audit.observability import log_event

_LOGGER = logging.getLogger(__name__)


class OpenAIEmbed:
    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        from openai import OpenAI

        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        t0 = time.monotonic()
        try:
            response = self._client.embeddings.create(model=self.model, input=texts)
            return [list(item.embedding) for item in response.data]
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            log_event(
                _LOGGER,
                logging.ERROR,
                "port_error",
                "embed failed",
                port="embed",
                model=self.model,
                http_status=status_code,
                latency_ms=int((time.monotonic() - t0) * 1000),
                exc_type=type(exc).__name__,
            )
            raise PortError("embed failed", port="embed", status_code=status_code) from exc
