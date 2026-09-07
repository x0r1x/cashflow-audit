from __future__ import annotations

from typer.testing import CliRunner

from cashflow_audit.cli import app


def test_ping_unset_exits_zero(monkeypatch) -> None:
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    result = CliRunner().invoke(app, ["ping"])
    assert result.exit_code == 0
    assert "unset" in result.stdout


def test_ping_help() -> None:
    result = CliRunner().invoke(app, ["ping", "--help"])
    assert result.exit_code == 0


def test_ping_down_exits_one(monkeypatch) -> None:
    from cashflow_audit import cli as cli_mod
    from cashflow_audit.api.probes import ProbeResult

    async def fake_checks(_settings, **_kwargs):
        return [
            (
                "llm_ping",
                ProbeResult(
                    configured=True,
                    reachable=False,
                    model_present=None,
                    error="http_503",
                ),
            ),
            (
                "embed_ping",
                ProbeResult(configured=False, reachable=False, model_present=None, error="unset"),
            ),
            (
                "redis",
                ProbeResult(configured=False, reachable=False, model_present=None, error="unset"),
            ),
        ]

    monkeypatch.setattr(cli_mod, "run_connectivity_checks", fake_checks)
    result = CliRunner().invoke(app, ["ping"])
    assert result.exit_code == 1


def test_ping_exit_follows_printed_ports(monkeypatch) -> None:
    from cashflow_audit import cli as cli_mod
    from cashflow_audit.api.probes import ProbeResult

    async def fake_checks(_settings, **_kwargs):
        return [
            (
                "llm_probe",
                ProbeResult(
                    configured=True,
                    reachable=True,
                    model_present=False,
                    error="model_missing",
                ),
            ),
            (
                "llm_ping",
                ProbeResult(configured=True, reachable=True, model_present=True, error=None),
            ),
            (
                "embed_ping",
                ProbeResult(configured=False, reachable=False, model_present=None, error="unset"),
            ),
            (
                "redis",
                ProbeResult(configured=False, reachable=False, model_present=None, error="unset"),
            ),
        ]

    monkeypatch.setattr(cli_mod, "run_connectivity_checks", fake_checks)
    result = CliRunner().invoke(app, ["ping"])
    assert result.exit_code == 0
    assert "llm: ok" in result.stdout
