from __future__ import annotations

from pathlib import Path

from cashflow_audit.ir.catalog import IrCatalog
from cashflow_audit.layout.detect import detect_layout
from cashflow_audit.layout.models import Layout
from cashflow_audit.store.fs import read_parquet, write_json


def layout_workbook(dest_dir: Path, catalog: IrCatalog | None = None) -> Layout:
    cells = read_parquet(dest_dir / "ir" / "cells.parquet")
    layout = detect_layout(cells)
    write_json(dest_dir / "layout.json", layout.model_dump(mode="json"))
    if catalog is not None:
        catalog.upsert_layout(
            axis_headers=_axis_rows(layout),
            layout_rows=_row_rows(layout),
        )
    return layout


def _axis_rows(layout: Layout) -> list[dict]:
    out: list[dict] = []
    for sheet in layout.sheets:
        for block in sheet.blocks:
            for header in block.axis.headers:
                out.append(
                    {
                        "sheet": sheet.name,
                        "block_id": block.block_id,
                        "col": header.col,
                        "role": header.role,
                        "header_text": header.text,
                        "period_key": header.period_key,
                    }
                )
    return out


def _row_rows(layout: Layout) -> list[dict]:
    out: list[dict] = []
    for sheet in layout.sheets:
        for block in sheet.blocks:
            for row in block.rows:
                out.append(
                    {
                        "sheet": sheet.name,
                        "block_id": block.block_id,
                        "row": row.row,
                        "label": row.label,
                        "parent_row": row.parent_row,
                        "check_row": row.check_row,
                        "label_col": block.label_col,
                        "indent": row.indent,
                    }
                )
    return out
