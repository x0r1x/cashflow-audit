from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from cashflow_audit.errors import PortError
from cashflow_audit.ports.protocols import ChatPort, EmbedPort

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _blank_to_none(value: object) -> object:
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _loopback_rewrite_host() -> str | None:
    return "host.docker.internal" if Path("/.dockerenv").exists() else None


def normalize_openai_base_url(url: str, *, rewrite_loopback_to: str | None = None) -> str:
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/")
    if path == "":
        path = "/v1"
    netloc = parts.netloc
    host = parts.hostname
    if rewrite_loopback_to and host in _LOOPBACK_HOSTS:
        userinfo = ""
        if parts.username:
            userinfo = parts.username
            if parts.password is not None:
                userinfo += f":{parts.password}"
            userinfo += "@"
        hostport = rewrite_loopback_to
        if parts.port is not None:
            hostport = f"{hostport}:{parts.port}"
        netloc = f"{userinfo}{hostport}"
    return urlunsplit((parts.scheme, netloc, path, parts.query, parts.fragment))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    redis_url: str | None = None
    data_dir: Path = Path("data")

    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str = "qwen3.6-27b-fp8"
    llm_tls_ca_file: Path | None = None
    llm_chat_path: str = "/chat/completions"

    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_model: str | None = None
    embedding_tls_ca_file: Path | None = None
    embedding_path: str = "/embeddings"

    worker_concurrency: int = 4
    max_inflight: int | None = None
    max_llm_inflight: int = 1
    max_embed_inflight: int = 4
    llm_slot_wait_sec: float = 120
    job_timeout_sec: float = 3600
    audit_ttl_days: int = 14
    job_llm_budget: int = 20
    job_embed_budget: int = 4

    log_level: str = "INFO"
    log_json: bool = True

    @field_validator(
        "redis_url",
        "llm_base_url",
        "llm_api_key",
        "embedding_base_url",
        "embedding_api_key",
        "embedding_model",
        "llm_tls_ca_file",
        "embedding_tls_ca_file",
        mode="before",
    )
    @classmethod
    def blank_str_to_none(cls, value: object) -> object:
        return _blank_to_none(value)

    @field_validator("llm_base_url", "embedding_base_url", mode="after")
    @classmethod
    def openai_compatible_base(cls, value: str | None) -> str | None:
        if not value:
            return None
        return normalize_openai_base_url(value, rewrite_loopback_to=_loopback_rewrite_host())

    @field_validator("llm_chat_path", mode="before")
    @classmethod
    def default_chat_path(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return "/chat/completions"
        return value.strip() if isinstance(value, str) else value

    @field_validator("embedding_path", mode="before")
    @classmethod
    def default_embed_path(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return "/embeddings"
        return value.strip() if isinstance(value, str) else value

    @property
    def inflight(self) -> int:
        return self.max_inflight if self.max_inflight is not None else self.worker_concurrency

    def require_redis(self) -> str:
        if not self.redis_url:
            raise RuntimeError("REDIS_URL required for serve")
        return self.redis_url

    def llm_configured(self) -> bool:
        return bool(self.llm_base_url)

    def embed_configured(self) -> bool:
        return bool(self.embedding_base_url and self.embedding_model)

    def chat(self) -> ChatPort | None:
        if not self.llm_configured():
            return None
        from cashflow_audit.adapters.openai_chat import OpenAIChat

        try:
            return OpenAIChat(
                base_url=self.llm_base_url,
                api_key=self.llm_api_key,
                model=self.llm_model,
                ca_file=self.llm_tls_ca_file,
                chat_path=self.llm_chat_path,
            )
        except PortError:
            return None

    def embed(self) -> EmbedPort | None:
        if not self.embed_configured():
            return None
        from cashflow_audit.adapters.openai_embed import OpenAIEmbed

        try:
            return OpenAIEmbed(
                base_url=self.embedding_base_url,
                api_key=self.embedding_api_key,
                model=self.embedding_model,
                ca_file=self.embedding_tls_ca_file,
                embed_path=self.embedding_path,
            )
        except PortError:
            return None
