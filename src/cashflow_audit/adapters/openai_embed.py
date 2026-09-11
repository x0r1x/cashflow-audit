from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx

from cashflow_audit.adapters.tls import httpx_verify, log_host
from cashflow_audit.errors import PortError
from cashflow_audit.observability import log_event

_LOGGER = logging.getLogger(__name__)


_PLACEHOLDER_KEY = "not-needed"


class OpenAIEmbed:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        ca_file: Path | None = None,
    ) -> None:
        from openai import OpenAI

        try:
            verify = httpx_verify(ca_file)
        except PortError as exc:
            log_event(
                _LOGGER,
                logging.ERROR,
                "port_error",
                "embed tls",
                port="embed",
                model=model,
                exc_type=type(exc).__name__,
            )
            raise PortError("tls ca file missing", port="embed") from exc
        http = httpx.Client(verify=verify)
        self._client = OpenAI(
            base_url=base_url, api_key=api_key or _PLACEHOLDER_KEY, http_client=http
        )
        self.model = model
        log_event(
            _LOGGER,
            logging.INFO,
            "port_connect",
            "embed client",
            port="embed",
            model=model,
            host=log_host(base_url),
            tls_ca=ca_file is not None,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        t0 = time.monotonic()
        try:
            response = self._client.embeddings.create(model=self.model, input=texts)
            vectors = [list(item.embedding) for item in response.data]
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
        log_event(
            _LOGGER,
            logging.INFO,
            "port_ok",
            "embed ok",
            port="embed",
            model=self.model,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        return vectors
