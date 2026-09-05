from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from cashflow_audit.ports.protocols import ChatPort, EmbedPort


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

    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_model: str | None = None

    worker_concurrency: int = 4
    max_inflight: int | None = None
    max_llm_inflight: int = 1
    max_embed_inflight: int = 4
    llm_slot_wait_sec: float = 120
    job_timeout_sec: float = 3600
    audit_ttl_days: int = 14
    job_llm_budget: int = 20
    job_embed_budget: int = 4

    @property
    def inflight(self) -> int:
        return self.max_inflight if self.max_inflight is not None else self.worker_concurrency

    def require_redis(self) -> str:
        if not self.redis_url:
            raise RuntimeError("REDIS_URL required for serve")
        return self.redis_url

    def chat(self) -> ChatPort | None:
        if not self.llm_base_url or not self.llm_api_key:
            return None
        from cashflow_audit.adapters.openai_chat import OpenAIChat

        return OpenAIChat(
            base_url=self.llm_base_url,
            api_key=self.llm_api_key,
            model=self.llm_model,
        )

    def embed(self) -> EmbedPort | None:
        if not self.embedding_base_url or not self.embedding_api_key or not self.embedding_model:
            return None
        from cashflow_audit.adapters.openai_embed import OpenAIEmbed

        return OpenAIEmbed(
            base_url=self.embedding_base_url,
            api_key=self.embedding_api_key,
            model=self.embedding_model,
        )
