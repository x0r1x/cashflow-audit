from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from cashflow_audit.adapters.slots import AlwaysGrant
from cashflow_audit.app.ids import audit_id_for, sha256_bytes
from cashflow_audit.app.pipeline import Pipeline
from cashflow_audit.errors import AuditError
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

    uvicorn.run(app_from_env(data_dir=data_dir), host=host, port=port)
