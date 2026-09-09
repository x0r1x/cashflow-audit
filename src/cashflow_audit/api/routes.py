from __future__ import annotations

import json
import logging
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Header, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from cashflow_audit.api.context import AppContext
from cashflow_audit.api.errors import ApiError
from cashflow_audit.app.hitl import apply_hitl
from cashflow_audit.app.ids import audit_id_for, sha256_bytes
from cashflow_audit.errors import AuditError
from cashflow_audit.explain.models import Report
from cashflow_audit.observability import log_event
from cashflow_audit.parse.zip_guard import open_xlsx_zip
from cashflow_audit.ports.protocols import JobState
from cashflow_audit.store.fs import atomic_write_bytes, write_json

router = APIRouter()
_LOGGER = logging.getLogger(__name__)
_MISSING = object()
_LAST_PORT_STATE: dict[str, bool | None] = {}

ActorHeader = Annotated[str | None, Header(alias="X-Actor-Id")]


class AnswerItem(BaseModel):
    question_id: str
    concept_id: str


class AnswersBody(BaseModel):
    answers: list[AnswerItem] = Field(default_factory=list)


def _ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def _actor(raw: str | None) -> str:
    if not raw or not raw.strip():
        raise ApiError(400, "missing_actor")
    return raw.strip()


def _live_or_report(audit_id: str, dest: Path, live: JobState | None) -> JSONResponse | None:
    if live is not None and live.status in {"queued", "running"}:
        return JSONResponse(
            {"audit_id": audit_id, "status": live.status, "stage": live.stage},
            status_code=202,
        )
    path = dest / "report.json"
    if not path.exists():
        return None
    report = Report.model_validate_json(path.read_text(encoding="utf-8"))
    return JSONResponse(
        {
            "audit_id": audit_id,
            "status": report.status,
            "stage": "done",
            "report_url": f"/v1/audits/{audit_id}/report",
        },
        status_code=200,
    )


def _owner(dest: Path, actor: str) -> dict[str, Any]:
    path = dest / "owner.json"
    if not path.exists():
        raise ApiError(404, "not_found")
    owner = json.loads(path.read_text(encoding="utf-8"))
    if str(owner.get("actor_id") or "") != actor:
        raise ApiError(403, "forbidden")
    return owner


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    ctx = _ctx(request)
    redis_ok = await ctx.bus.ping()
    if not redis_ok:
        return JSONResponse(
            {"status": "not_ready", "redis": False, "llm": False, "embeddings": False},
            status_code=503,
        )
    llm_state = await _port_state(ctx.llm_ok, ctx.llm_probe, port="llm")
    embed_state = await _port_state(ctx.embed_ok, ctx.embed_probe, port="embed")
    status = "ready"
    if llm_state is False or embed_state is False:
        status = "degraded"
    return JSONResponse(
        {
            "status": status,
            "redis": True,
            "llm": bool(llm_state),
            "embeddings": bool(embed_state),
        },
        status_code=200,
    )


@router.post("/v1/audits")
async def post_audit(
    request: Request,
    file: Annotated[UploadFile | None, File()] = None,
    x_actor_id: ActorHeader = None,
) -> JSONResponse:
    actor = _actor(x_actor_id)
    ctx = _ctx(request)
    if file is None:
        raise AuditError("empty_file")
    filename = file.filename or "upload.xlsx"
    data = await file.read()
    _validate_upload(filename, data, ctx.max_upload_bytes)
    digest = sha256_bytes(data)
    audit_id = audit_id_for(actor, digest)
    request.state.audit_id = audit_id
    dest = ctx.store.dest_dir(audit_id)
    live = await ctx.bus.get_live(audit_id)
    ready = _live_or_report(audit_id, dest, live)
    if ready is not None:
        return ready
    dest.mkdir(parents=True, exist_ok=True)
    source = dest / "source.xlsx"
    if not source.exists():
        atomic_write_bytes(source, data)
    owner_path = dest / "owner.json"
    if not owner_path.exists():
        write_json(
            owner_path,
            {
                "actor_id": actor,
                "content_sha256": digest,
                "source_filename": Path(filename).name,
            },
        )
    live = await ctx.bus.get_live(audit_id)
    ready = _live_or_report(audit_id, dest, live)
    if ready is not None:
        return ready
    await ctx.bus.mark_queued(audit_id, actor, replace_terminal=False)
    await ctx.bus.enqueue(audit_id)
    return JSONResponse(
        {"audit_id": audit_id, "status": "queued", "stage": "queued"},
        status_code=202,
    )


@router.get("/v1/audits/{audit_id}")
async def get_audit(
    request: Request,
    audit_id: str,
    x_actor_id: ActorHeader = None,
) -> JSONResponse:
    actor = _actor(x_actor_id)
    ctx = _ctx(request)
    request.state.audit_id = audit_id
    dest = ctx.store.dest_dir(audit_id)
    owner = _owner(dest, actor)
    status, stage, error = await _resolve_status(ctx, dest, audit_id)
    body: dict[str, object] = {
        "audit_id": audit_id,
        "status": status,
        "stage": stage,
        "source_filename": owner.get("source_filename"),
        "error": error,
    }
    if (dest / "report.json").exists() and status not in {"queued", "running"}:
        body["report_url"] = f"/v1/audits/{audit_id}/report"
    headers = {}
    if status in {"queued", "running"}:
        headers["Retry-After"] = "2"
    return JSONResponse(body, status_code=200, headers=headers)


@router.get("/v1/audits/{audit_id}/report")
async def get_report(
    request: Request,
    audit_id: str,
    x_actor_id: ActorHeader = None,
) -> JSONResponse:
    actor = _actor(x_actor_id)
    ctx = _ctx(request)
    request.state.audit_id = audit_id
    dest = ctx.store.dest_dir(audit_id)
    _owner(dest, actor)
    path = dest / "report.json"
    if not path.exists():
        status, stage, _error = await _resolve_status(ctx, dest, audit_id)
        raise ApiError(409, "report_not_ready", status=status, stage=stage)
    report = json.loads(path.read_text(encoding="utf-8"))
    return JSONResponse(report, status_code=200)


@router.post("/v1/audits/{audit_id}/answers")
async def post_answers(
    request: Request,
    audit_id: str,
    body: AnswersBody,
    x_actor_id: ActorHeader = None,
) -> JSONResponse:
    actor = _actor(x_actor_id)
    ctx = _ctx(request)
    request.state.audit_id = audit_id
    dest = ctx.store.dest_dir(audit_id)
    _owner(dest, actor)
    live = await ctx.bus.get_live(audit_id)
    if live and live.status in {"queued", "running"}:
        raise ApiError(409, "already_running")
    report_path = dest / "report.json"
    if not report_path.exists():
        raise ApiError(409, "report_not_ready")
    report = Report.model_validate_json(report_path.read_text(encoding="utf-8"))
    if not report.questions:
        raise ApiError(409, "no_questions")
    by_id = {q.id: q for q in report.questions}
    for item in body.answers:
        question = by_id.get(item.question_id)
        if question is None or item.concept_id not in question.options:
            raise ApiError(422, "invalid_answer")
    payload = [item.model_dump() for item in body.answers]
    locked = await ctx.bus.acquire_glossary(actor)
    if not locked:
        raise ApiError(409, "already_running")
    try:
        apply_hitl(dest, payload, actor_id=actor, glossary_dir=ctx.store.glossary_dir())
    finally:
        await ctx.bus.release_glossary(actor)
    await ctx.bus.mark_queued(audit_id, actor, replace_terminal=True)
    await ctx.bus.enqueue(audit_id)
    return JSONResponse(
        {"audit_id": audit_id, "status": "queued", "stage": "queued"},
        status_code=202,
    )


async def _resolve_status(
    ctx: AppContext, dest: Path, audit_id: str
) -> tuple[str, str, str | None]:
    live = await ctx.bus.get_live(audit_id)
    if live:
        return live.status, live.stage, live.error
    meta_path = dest / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        status = str(meta.get("status") or "failed")
        stage = str(meta.get("stage") or "done")
        return status, stage, meta.get("error")
    if (dest / "report.json").exists():
        report = Report.model_validate_json((dest / "report.json").read_text(encoding="utf-8"))
        return report.status, "done", None
    return "queued", "queued", None


async def _port_state(
    flag: bool | None,
    probe: Callable[[], Awaitable[bool | None]] | None,
    *,
    port: str,
) -> bool | None:
    if flag is not None:
        return flag
    if probe is None:
        return None
    value = await probe()
    if getattr(probe, "emits_probe_log", False):
        return value
    prev = _LAST_PORT_STATE.get(port, _MISSING)
    if prev is _MISSING or prev != value:
        log_event(
            _LOGGER,
            logging.INFO,
            "probe_models",
            "port probe",
            port=port,
            reachable=value,
        )
        _LAST_PORT_STATE[port] = value
    return value


def _validate_upload(filename: str, data: bytes, max_bytes: int) -> None:
    if not data:
        raise AuditError("empty_file")
    if len(data) > max_bytes:
        raise AuditError("file_too_large")
    suffix = Path(filename).suffix.lower()
    if suffix not in {".xlsx", ".xlsm"}:
        raise AuditError("unsupported_media_type", "ожидается .xlsx или .xlsm")
    _peek_zip(data)


def _peek_zip(data: bytes) -> None:
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=True) as tmp:
        tmp.write(data)
        tmp.flush()
        zf = open_xlsx_zip(Path(tmp.name))
        zf.close()
