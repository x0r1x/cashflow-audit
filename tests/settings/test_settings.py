from __future__ import annotations

from pathlib import Path

from cashflow_audit.settings import Settings


def test_defaults_without_env(monkeypatch) -> None:
    for key in (
        "REDIS_URL",
        "DATA_DIR",
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_MODEL",
        "EMBEDDING_BASE_URL",
        "EMBEDDING_API_KEY",
        "EMBEDDING_MODEL",
        "WORKER_CONCURRENCY",
        "MAX_INFLIGHT",
        "JOB_TIMEOUT_SEC",
        "JOB_LLM_BUDGET",
        "JOB_EMBED_BUDGET",
        "LOG_LEVEL",
        "LOG_JSON",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    assert settings.redis_url is None
    assert settings.data_dir == Path("data")
    assert settings.worker_concurrency == 4
    assert settings.inflight == 4
    assert settings.job_timeout_sec == 3600
    assert settings.job_llm_budget == 20
    assert settings.job_embed_budget == 4
    assert settings.llm_model == "qwen3.6-27b-fp8"
    assert settings.log_level == "INFO"
    assert settings.log_json is True
    assert settings.chat() is None
    assert settings.embed() is None


def test_reads_process_env(monkeypatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setenv("WORKER_CONCURRENCY", "8")
    monkeypatch.setenv("MAX_INFLIGHT", "3")
    monkeypatch.setenv("DATA_DIR", "/tmp/cf")
    settings = Settings(_env_file=None)
    assert settings.require_redis() == "redis://127.0.0.1:6379/0"
    assert settings.worker_concurrency == 8
    assert settings.inflight == 3
    assert settings.data_dir == Path("/tmp/cf")


def test_embed_needs_own_model_not_llm(monkeypatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "http://llm")
    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://emb")
    monkeypatch.setenv("EMBEDDING_API_KEY", "emb-key")
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    settings = Settings(_env_file=None)
    assert settings.llm_base_url == "http://llm"
    assert settings.embedding_base_url == "http://emb"
    assert settings.embed() is None


def test_require_redis_without_url(monkeypatch) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)
    settings = Settings(_env_file=None)
    try:
        settings.require_redis()
    except RuntimeError as exc:
        assert "REDIS_URL" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
