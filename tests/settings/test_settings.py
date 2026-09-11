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
        "LLM_TLS_CA_FILE",
        "LLM_CHAT_PATH",
        "EMBEDDING_TLS_CA_FILE",
        "EMBEDDING_PATH",
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
    assert settings.llm_tls_ca_file is None
    assert settings.embedding_tls_ca_file is None
    assert settings.llm_chat_path == "/chat/completions"
    assert settings.embedding_path == "/embeddings"


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


def test_missing_tls_ca_does_not_raise_from_chat_factory(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_TLS_CA_FILE", str(tmp_path / "missing.pem"))
    settings = Settings(_env_file=None)
    assert settings.llm_configured() is True
    assert settings.chat() is None


def test_llm_url_without_key_is_configured(monkeypatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "http://llm/v1")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    settings = Settings(_env_file=None)
    assert settings.llm_configured() is True
    assert settings.llm_api_key is None
    assert settings.chat() is not None


def test_empty_api_key_env_is_unset_key_not_port(monkeypatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "http://llm/v1")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://emb/v1")
    monkeypatch.setenv("EMBEDDING_API_KEY", "  ")
    monkeypatch.setenv("EMBEDDING_MODEL", "e5")
    settings = Settings(_env_file=None)
    assert settings.llm_api_key is None
    assert settings.embedding_api_key is None
    assert settings.llm_configured() is True
    assert settings.embed_configured() is True
    assert settings.embed() is not None


def test_api_v1_prefix_is_kept(monkeypatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:1234/api/v1")
    settings = Settings(_env_file=None)
    assert settings.llm_base_url == "http://localhost:1234/api/v1"


def test_empty_chat_path_env_is_default(monkeypatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "http://llm/v1")
    monkeypatch.setenv("LLM_CHAT_PATH", "")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://emb/v1")
    monkeypatch.setenv("EMBEDDING_MODEL", "e5")
    monkeypatch.setenv("EMBEDDING_PATH", "  ")
    settings = Settings(_env_file=None)
    assert settings.llm_chat_path == "/chat/completions"
    assert settings.embedding_path == "/embeddings"


def test_invalid_chat_path_does_not_raise_from_factory(monkeypatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "http://llm/v1")
    monkeypatch.setenv("LLM_CHAT_PATH", "http://evil/chat")
    settings = Settings(_env_file=None)
    assert settings.llm_configured() is True
    assert settings.chat() is None


def test_origin_without_v1_gets_openai_prefix(monkeypatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1234")
    settings = Settings(_env_file=None)
    assert settings.llm_base_url == "http://127.0.0.1:1234/v1"


def test_loopback_rewritten_when_docker_host_set(monkeypatch) -> None:
    monkeypatch.setattr(
        "cashflow_audit.settings._loopback_rewrite_host",
        lambda: "host.docker.internal",
    )
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:1234/api/v1")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://127.0.0.1:1234")
    monkeypatch.setenv("EMBEDDING_MODEL", "e5")
    settings = Settings(_env_file=None)
    assert settings.llm_base_url == "http://host.docker.internal:1234/api/v1"
    assert settings.embedding_base_url == "http://host.docker.internal:1234/v1"


def test_embed_needs_own_model_not_llm(monkeypatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "http://llm")
    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://emb")
    monkeypatch.setenv("EMBEDDING_API_KEY", "emb-key")
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    settings = Settings(_env_file=None)
    assert settings.llm_base_url == "http://llm/v1"
    assert settings.embedding_base_url == "http://emb/v1"
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
