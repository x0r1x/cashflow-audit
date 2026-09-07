from __future__ import annotations

import logging
import time

from pydantic import BaseModel

from cashflow_audit.errors import PortError
from cashflow_audit.observability import log_event

_LOGGER = logging.getLogger(__name__)


class OpenAIChat:
    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        from openai import OpenAI

        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self.model = model

    def complete_json(self, schema: type[BaseModel], messages: list) -> BaseModel:
        t0 = time.monotonic()
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or "{}"
            return schema.model_validate_json(content)
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            log_event(
                _LOGGER,
                logging.ERROR,
                "port_error",
                "chat failed",
                port="chat",
                model=self.model,
                http_status=status_code,
                latency_ms=int((time.monotonic() - t0) * 1000),
                exc_type=type(exc).__name__,
            )
            raise PortError("chat failed", port="chat", status_code=status_code) from exc
