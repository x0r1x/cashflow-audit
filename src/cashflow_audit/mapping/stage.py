from __future__ import annotations

import json
import os
from pathlib import Path

from cashflow_audit.layout.models import Layout
from cashflow_audit.mapping.cascade import map_layout
from cashflow_audit.mapping.models import Concept, MappingDocument
from cashflow_audit.mapping.taxonomy import load_taxonomy
from cashflow_audit.ports.protocols import ChatPort, EmbedPort, SlotGate
from cashflow_audit.store.fs import read_parquet, write_json


def mapping_workbook(
    dest_dir: Path,
    *,
    embed: EmbedPort | None = None,
    chat: ChatPort | None = None,
    slots: SlotGate | None = None,
    glossary: dict[tuple[str, str], str] | None = None,
    taxonomy: list[Concept] | None = None,
    cache_path: Path | None = None,
) -> MappingDocument:
    path = dest_dir / "mapping.json"
    if path.exists():
        return MappingDocument.model_validate_json(path.read_text(encoding="utf-8"))
    layout = Layout.model_validate(
        json.loads((dest_dir / "layout.json").read_text(encoding="utf-8"))
    )
    cells: list[dict] = []
    ir_cells = dest_dir / "ir" / "cells.parquet"
    if ir_cells.exists():
        cells = read_parquet(ir_cells)
    timeout = float(os.environ.get("LLM_SLOT_WAIT_SEC", "120"))
    doc = map_layout(
        layout,
        taxonomy=taxonomy or load_taxonomy(),
        glossary=glossary or {},
        embed=embed,
        chat=chat,
        slots=slots,
        cells=cells,
        slot_timeout_sec=timeout,
        cache_path=cache_path,
        embedding_model=os.environ.get("EMBEDDING_MODEL", ""),
    )
    write_json(path, doc.model_dump(mode="json"))
    return doc
