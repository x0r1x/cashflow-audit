from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from cashflow_audit.checkers.context import CheckContext
from cashflow_audit.checkers.models import CheckDocument
from cashflow_audit.checkers.run import run_checks
from cashflow_audit.compile.csr import build_csr
from cashflow_audit.compile.models import Edge
from cashflow_audit.ir.catalog import IrCatalog
from cashflow_audit.layout.models import Layout
from cashflow_audit.mapping.models import MappingDocument
from cashflow_audit.observability import log_event
from cashflow_audit.series.models import SeriesOutlier
from cashflow_audit.store.fs import read_parquet, write_json

_LOGGER = logging.getLogger("cashflow_audit.checkers")


def check_workbook(dest_dir: Path, catalog: IrCatalog | None = None) -> CheckDocument:
    path = dest_dir / "candidates.json"
    if path.exists():
        log_event(_LOGGER, logging.INFO, "stage_skip", "artifact exists", stage="check")
        return CheckDocument.model_validate_json(path.read_text(encoding="utf-8"))
    t0 = time.monotonic()
    cells = read_parquet(dest_dir / "ir" / "cells.parquet")
    edge_rows = read_parquet(dest_dir / "ir" / "edges.parquet")
    edges = [Edge.model_validate(_edge_dict(row)) for row in edge_rows]
    extra = [f"{c['sheet']}!{c['addr']}" for c in cells]
    workbook = json.loads((dest_dir / "raw" / "workbook.json").read_text(encoding="utf-8"))
    layout = Layout.model_validate_json((dest_dir / "layout.json").read_text(encoding="utf-8"))
    mapping = MappingDocument.model_validate_json(
        (dest_dir / "mapping.json").read_text(encoding="utf-8")
    )
    series = _load_series(catalog)
    ctx = CheckContext(
        cells=cells,
        edges=edges,
        workbook=workbook,
        layout=layout,
        mapping=mapping,
        series_outliers=series,
        csr=build_csr(edges, extra_nodes=extra),
    )
    doc = run_checks(ctx)
    write_json(path, doc.model_dump(mode="json"))
    log_event(
        _LOGGER,
        logging.INFO,
        "stage_done",
        "check done",
        stage="check",
        duration_ms=int((time.monotonic() - t0) * 1000),
    )
    return doc


def _edge_dict(row: dict) -> dict:
    return {
        "kind": row["kind"],
        "source": row["source"],
        "target": row.get("target"),
        "unresolved": bool(row.get("unresolved")),
        "truncated": bool(row.get("truncated")),
    }


def _load_series(catalog: IrCatalog | None) -> list[SeriesOutlier]:
    if catalog is None:
        return []
    try:
        rel = catalog.sql("SELECT * FROM series_outliers")
        cols = [d[0] for d in rel.description]
        rows = [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]
    except Exception:
        return []
    return [SeriesOutlier.model_validate(row) for row in rows]
