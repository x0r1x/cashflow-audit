from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from cashflow_audit.adapters.slots import AlwaysGrant
from cashflow_audit.api.probes import ProbeResult, run_connectivity_checks
from cashflow_audit.app.ids import audit_id_for, sha256_bytes
from cashflow_audit.app.pipeline import Pipeline
from cashflow_audit.errors import AuditError
from cashflow_audit.observability import configure_logging
from cashflow_audit.settings import Settings
from cashflow_audit.store.fs import atomic_write_bytes, write_json

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.command()
def audit(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    output: Annotated[Path, typer.Option("-o", "--output")],
    data_dir: Annotated[Path | None, typer.Option("--data-dir")] = None,
) -> None:
    if source.suffix.lower() not in {".xlsx", ".xlsm"}:
        typer.echo("ожидается .xlsx или .xlsm", err=True)
        raise typer.Exit(code=1)
    settings = Settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    root = data_dir or settings.data_dir
    data = source.read_bytes()
    if not data:
        typer.echo("empty_file", err=True)
        raise typer.Exit(code=1)
    actor = "anonymous"
    digest = sha256_bytes(data)
    audit_id = audit_id_for(actor, digest)
    dest = root / "audits" / audit_id
    dest.mkdir(parents=True, exist_ok=True)
    dest_source = dest / "source.xlsx"
    if not dest_source.exists():
        atomic_write_bytes(dest_source, data)
    owner_path = dest / "owner.json"
    if not owner_path.exists():
        write_json(
            owner_path,
            {
                "actor_id": actor,
                "content_sha256": digest,
                "source_filename": source.name,
            },
        )
    try:
        report = Pipeline(
            chat=settings.chat(),
            embed=settings.embed(),
            slots=AlwaysGrant(),
            glossary_dir=root / "glossary",
            settings=settings,
        ).run(dest_source, dest, actor_id=actor)
    except AuditError as exc:
        typer.echo(exc.code, err=True)
        raise typer.Exit(code=1) from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)
    output.write_text(payload + "\n")


@app.command()
def serve(
    host: Annotated[str, typer.Option("--host")] = "0.0.0.0",  # noqa: B104
    port: Annotated[int, typer.Option("--port")] = 8080,
    data_dir: Annotated[Path | None, typer.Option("--data-dir")] = None,
) -> None:
    import uvicorn

    from cashflow_audit.api.app import app_from_env

    settings = Settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    uvicorn.run(app_from_env(data_dir=data_dir), host=host, port=port)


def _format_ping_line(name: str, result: ProbeResult, model: str | None) -> str:
    if result.ok is None:
        return f"{name}: unset"
    if result.ok:
        parts = [f"{name}: ok"]
        if model:
            parts.append(f"model={model}")
        if result.latency_ms is not None:
            parts.append(f"latency_ms={result.latency_ms}")
        return " ".join(parts)
    error = result.error or "down"
    return f"{name}: down error={error}"


async def _ping_exit(settings: Settings) -> int:
    results = await run_connectivity_checks(settings)
    by_name = dict(results)
    llm = by_name.get("llm_ping") or ProbeResult(
        configured=False, reachable=False, model_present=None, error="unset"
    )
    embed = by_name.get("embed_ping") or ProbeResult(
        configured=False, reachable=False, model_present=None, error="unset"
    )
    redis = by_name.get("redis") or ProbeResult(
        configured=False, reachable=False, model_present=None, error="unset"
    )
    typer.echo(_format_ping_line("llm", llm, settings.llm_model if llm.configured else None))
    typer.echo(
        _format_ping_line("embed", embed, settings.embedding_model if embed.configured else None)
    )
    typer.echo(_format_ping_line("redis", redis, None))
    if any(result.ok is False for _, result in results):
        return 1
    return 0


@app.command("ping")
def ping_cmd() -> None:
    settings = Settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    raise typer.Exit(code=asyncio.run(_ping_exit(settings)))
