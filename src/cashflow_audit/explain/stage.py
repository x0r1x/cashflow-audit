from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from cashflow_audit.checkers.models import CheckDocument
from cashflow_audit.explain.compose import compose_report
from cashflow_audit.explain.models import JobMeta, Report
from cashflow_audit.lineage.models import LineageDocument
from cashflow_audit.mapping.models import MappingDocument
from cashflow_audit.observability import log_event
from cashflow_audit.ports.protocols import ChatPort, SlotGate
from cashflow_audit.store.fs import read_parquet, write_json

_LOGGER = logging.getLogger("cashflow_audit.explain")


def explain_workbook(
    dest_dir: Path,
    *,
    chat: ChatPort | None = None,
    slots: SlotGate | None = None,
    slot_timeout_sec: float = 120.0,
    llm_model: str | None = None,
    embedding_model: str | None = None,
) -> Report:
    report_path = dest_dir / "report.json"
    integrity_path = dest_dir / "integrity.json"
    meta_path = dest_dir / "meta.json"
    if report_path.exists() and integrity_path.exists() and meta_path.exists():
        log_event(_LOGGER, logging.INFO, "stage_skip", "artifact exists", stage="explain")
        return Report.model_validate_json(report_path.read_text(encoding="utf-8"))
    t0 = time.monotonic()
    owner = {}
    owner_path = dest_dir / "owner.json"
    if owner_path.exists():
        owner = json.loads(owner_path.read_text(encoding="utf-8"))
    check = CheckDocument.model_validate_json(
        (dest_dir / "candidates.json").read_text(encoding="utf-8")
    )
    lineage = LineageDocument.model_validate_json(
        (dest_dir / "lineage.json").read_text(encoding="utf-8")
    )
    mapping = MappingDocument.model_validate_json(
        (dest_dir / "mapping.json").read_text(encoding="utf-8")
    )
    ir_refs = {
        f"{row['sheet']}!{row['addr']}"
        for row in read_parquet(dest_dir / "ir" / "cells.parquet")
    }
    embeddings_used = any(row.source == "embed" for row in mapping.rows)
    report = compose_report(
        candidates=check.candidates,
        lineage=lineage,
        mapping=mapping,
        check=check,
        ir_refs=ir_refs,
        chat=chat,
        slots=slots,
        embeddings_used=embeddings_used,
        audit_id=dest_dir.name,
        source_filename=str(owner.get("source_filename") or ""),
        sha256=str(owner.get("content_sha256") or ""),
        llm_model=llm_model,
        embedding_model=embedding_model,
        slot_timeout_sec=slot_timeout_sec,
    )
    write_json(report_path, report.model_dump(mode="json"))
    write_json(
        integrity_path,
        {
            "findings": [
                item.model_dump(mode="json")
                for item in report.findings
                if not item.detector.startswith("risk.")
                and not item.detector.startswith("frs.")
            ]
        },
    )
    write_json(
        meta_path,
        JobMeta(status=report.status, stage="done", error=None).model_dump(mode="json"),
    )
    log_event(
        _LOGGER,
        logging.INFO,
        "stage_done",
        "explain done",
        stage="explain",
        duration_ms=int((time.monotonic() - t0) * 1000),
    )
    return report
