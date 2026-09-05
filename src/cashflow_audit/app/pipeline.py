from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path

from cashflow_audit.checkers.stage import check_workbook
from cashflow_audit.compile.stage import compile_workbook
from cashflow_audit.errors import AuditError, PortError
from cashflow_audit.explain.models import JobMeta, Report, ReportSummary
from cashflow_audit.explain.stage import explain_workbook
from cashflow_audit.ir.catalog import IrCatalog
from cashflow_audit.layout.models import Layout
from cashflow_audit.layout.stage import layout_workbook, register_layout
from cashflow_audit.lineage.stage import lineage_workbook
from cashflow_audit.mapping.stage import mapping_workbook
from cashflow_audit.parse.stage import parse_workbook
from cashflow_audit.ports.protocols import ChatPort, EmbedPort, SlotGate
from cashflow_audit.series.stage import series_workbook
from cashflow_audit.settings import Settings
from cashflow_audit.store.fs import write_json
from cashflow_audit.store.glossary import load_glossary


class PipelineTimeout(Exception):
    pass


class Pipeline:
    def __init__(
        self,
        *,
        embed: EmbedPort | None = None,
        chat: ChatPort | None = None,
        slots: SlotGate | None = None,
        glossary_dir: Path | None = None,
        timeout_sec: float | None = None,
        settings: Settings | None = None,
        clock: Callable[[], float] = time.monotonic,
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        self.embed = embed
        self.chat = chat
        self.slots = slots
        self.glossary_dir = glossary_dir
        self.settings = settings
        self.timeout_sec = (
            timeout_sec
            if timeout_sec is not None
            else (settings.job_timeout_sec if settings is not None else 3600.0)
        )
        self.slot_timeout_sec = settings.llm_slot_wait_sec if settings is not None else 120.0
        self.llm_model = settings.llm_model if settings is not None else None
        self.embedding_model = (
            settings.embedding_model or "" if settings is not None else ""
        )
        self.clock = clock
        self.on_progress = on_progress
        self.catalog: IrCatalog | None = None
        self._started = 0.0

    def run(
        self,
        source: Path,
        dest_dir: Path,
        *,
        actor_id: str = "anonymous",
    ) -> Report:
        dest_dir.mkdir(parents=True, exist_ok=True)
        self._started = self.clock()
        self.catalog = None
        owner = _ensure_owner(source, dest_dir, actor_id)
        stage = "parse"
        try:
            stage = self._enter("parse")
            _parse(source, dest_dir)
            stage = self._enter("compile")
            _compile(dest_dir)
            self.catalog = IrCatalog.open(dest_dir)
            stage = self._enter("layout")
            _layout(dest_dir, self.catalog)
            stage = self._enter("series")
            series_workbook(dest_dir, self.catalog)
            stage = self._enter("mapping")
            glossary = {}
            if self.glossary_dir is not None:
                glossary = load_glossary(self.glossary_dir / f"{actor_id}.json")
            cache_path = None
            if self.glossary_dir is not None:
                cache_path = self.glossary_dir.parent / "taxonomy_embeddings.npz"
            mapping_workbook(
                dest_dir,
                embed=self.embed,
                chat=self.chat,
                slots=self.slots,
                glossary=glossary,
                cache_path=cache_path,
                slot_timeout_sec=self.slot_timeout_sec,
                embedding_model=self.embedding_model,
            )
            stage = self._enter("check")
            check_workbook(dest_dir, self.catalog)
            stage = self._enter("lineage")
            lineage_workbook(dest_dir)
            stage = self._enter("explain")
            return explain_workbook(
                dest_dir,
                chat=self.chat,
                slots=self.slots,
                slot_timeout_sec=self.slot_timeout_sec,
                llm_model=self.llm_model,
                embedding_model=self.embedding_model or None,
            )
        except PipelineTimeout:
            return _fail(dest_dir, owner, "timeout", stage, self.slots)
        except AuditError as exc:
            return _fail(dest_dir, owner, exc.code, stage, self.slots)
        except PortError:
            return _fail(dest_dir, owner, "port_error", stage, self.slots)
        finally:
            if self.catalog is not None:
                self.catalog.close()
                self.catalog = None

    def _enter(self, stage: str) -> str:
        if self.on_progress:
            self.on_progress(stage)
        if self.clock() - self._started > self.timeout_sec:
            raise PipelineTimeout(stage)
        return stage


def _parse(source: Path, dest_dir: Path) -> None:
    raw_ok = (dest_dir / "raw" / "cells.parquet").exists() and (
        dest_dir / "raw" / "workbook.json"
    ).exists()
    if raw_ok:
        return
    dest_source = dest_dir / "source.xlsx"
    if not dest_source.exists():
        dest_source.write_bytes(source.read_bytes())
    parse_workbook(dest_source, dest_dir)


def _compile(dest_dir: Path) -> None:
    if (dest_dir / "ir" / "cells.parquet").exists() and (
        dest_dir / "ir" / "edges.parquet"
    ).exists():
        return
    compile_workbook(dest_dir)


def _layout(dest_dir: Path, catalog: IrCatalog) -> None:
    path = dest_dir / "layout.json"
    if path.exists():
        layout = Layout.model_validate_json(path.read_text(encoding="utf-8"))
        register_layout(layout, catalog)
        return
    layout_workbook(dest_dir, catalog)


def _ensure_owner(source: Path, dest_dir: Path, actor_id: str) -> dict:
    path = dest_dir / "owner.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    sha = hashlib.sha256(source.read_bytes()).hexdigest() if source.exists() else ""
    owner = {
        "actor_id": actor_id,
        "content_sha256": sha,
        "source_filename": source.name,
    }
    write_json(path, owner)
    return owner


def _fail(
    dest_dir: Path,
    owner: dict,
    error: str,
    stage: str,
    slots: SlotGate | None,
) -> Report:
    write_json(
        dest_dir / "meta.json",
        JobMeta(status="failed", stage=stage, error=error).model_dump(mode="json"),
    )
    if slots is not None:
        slots.release("run")
    return Report(
        audit_id=dest_dir.name,
        source_filename=str(owner.get("source_filename") or ""),
        sha256=str(owner.get("content_sha256") or ""),
        status="failed",
        llm_used=False,
        embeddings_used=False,
        summary=ReportSummary(
            findings=0,
            by_severity={"error": 0, "warning": 0, "risk": 0},
            questions=0,
        ),
        findings=[],
        questions=[],
    )
