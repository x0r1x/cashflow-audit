from __future__ import annotations

import logging
import time
from pathlib import Path

from cashflow_audit.frs.models import FrsDocument
from cashflow_audit.frs.router import run_frs
from cashflow_audit.layout.models import Layout
from cashflow_audit.mapping.models import MappingDocument
from cashflow_audit.observability import log_event
from cashflow_audit.store.fs import read_parquet, write_json

_LOGGER = logging.getLogger("cashflow_audit.frs")


def frs_workbook(dest_dir: Path) -> FrsDocument:
    path = dest_dir / "frs.json"
    if path.exists():
        log_event(_LOGGER, logging.INFO, "stage_skip", "artifact exists", stage="frs")
        return FrsDocument.model_validate_json(path.read_text(encoding="utf-8"))
    t0 = time.monotonic()
    mapping = MappingDocument.model_validate_json(
        (dest_dir / "mapping.json").read_text(encoding="utf-8")
    )
    layout = None
    layout_path = dest_dir / "layout.json"
    if layout_path.exists():
        layout = Layout.model_validate_json(layout_path.read_text(encoding="utf-8"))
    cells: list[dict] = []
    cells_path = dest_dir / "ir" / "cells.parquet"
    if cells_path.exists():
        cells = read_parquet(cells_path)
    doc = run_frs(mapping, layout=layout, cells=cells)
    write_json(path, doc.model_dump(mode="json"))
    log_event(
        _LOGGER,
        logging.INFO,
        "stage_done",
        "frs done",
        stage="frs",
        duration_ms=int((time.monotonic() - t0) * 1000),
    )
    return doc
