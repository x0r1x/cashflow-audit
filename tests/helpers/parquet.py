from __future__ import annotations

import json
from pathlib import Path

import duckdb


def load_cells(audit_dir: Path) -> list[dict]:
    parquet = audit_dir / "raw" / "cells.parquet"
    con = duckdb.connect(":memory:")
    try:
        rel = con.execute(f"SELECT * FROM read_parquet('{parquet.as_posix()}')")
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]
    finally:
        con.close()


def load_workbook_json(audit_dir: Path) -> dict:
    return json.loads((audit_dir / "raw" / "workbook.json").read_text(encoding="utf-8"))
