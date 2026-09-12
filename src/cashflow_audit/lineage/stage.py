from __future__ import annotations

import logging
import time
from pathlib import Path

from cashflow_audit.checkers.models import CheckDocument
from cashflow_audit.compile.csr import build_csr
from cashflow_audit.compile.models import Edge
from cashflow_audit.frs.candidates import issues_as_candidates
from cashflow_audit.frs.models import FrsDocument
from cashflow_audit.lineage.models import LineageDocument
from cashflow_audit.lineage.trace import trace_lineage
from cashflow_audit.mapping.models import MappingDocument
from cashflow_audit.observability import log_event
from cashflow_audit.store.fs import read_parquet, write_json

_LOGGER = logging.getLogger("cashflow_audit.lineage")


def lineage_workbook(dest_dir: Path) -> LineageDocument:
    path = dest_dir / "lineage.json"
    if path.exists():
        log_event(_LOGGER, logging.INFO, "stage_skip", "artifact exists", stage="lineage")
        return LineageDocument.model_validate_json(path.read_text(encoding="utf-8"))
    t0 = time.monotonic()
    check = CheckDocument.model_validate_json(
        (dest_dir / "candidates.json").read_text(encoding="utf-8")
    )
    mapping = MappingDocument.model_validate_json(
        (dest_dir / "mapping.json").read_text(encoding="utf-8")
    )
    cells = read_parquet(dest_dir / "ir" / "cells.parquet")
    edge_rows = read_parquet(dest_dir / "ir" / "edges.parquet")
    edges = [
        Edge.model_validate(
            {
                "kind": row["kind"],
                "source": row["source"],
                "target": row.get("target"),
                "unresolved": bool(row.get("unresolved")),
                "truncated": bool(row.get("truncated")),
            }
        )
        for row in edge_rows
    ]
    extra = [f"{c['sheet']}!{c['addr']}" for c in cells]
    csr = build_csr(edges, extra_nodes=extra)
    frs_cands = []
    frs_path = dest_dir / "frs.json"
    if frs_path.exists():
        frs_cands = issues_as_candidates(
            FrsDocument.model_validate_json(frs_path.read_text(encoding="utf-8"))
        )
    doc = trace_lineage([*check.candidates, *frs_cands], csr=csr, mapping=mapping)
    write_json(path, doc.model_dump(mode="json"))
    log_event(
        _LOGGER,
        logging.INFO,
        "stage_done",
        "lineage done",
        stage="lineage",
        duration_ms=int((time.monotonic() - t0) * 1000),
    )
    return doc
