from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

_CELL_COLUMNS = (
    "sheet",
    "row",
    "col",
    "addr",
    "formula_raw",
    "cached_value",
    "hidden",
    "number_format",
    "comment",
)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def write_json(path: Path, data: dict[str, Any]) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    atomic_write_bytes(path, payload.encode("utf-8"))


def write_cells_parquet(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    con = duckdb.connect(":memory:")
    try:
        con.execute(
            """
            CREATE TABLE cells (
                sheet VARCHAR,
                row INTEGER,
                col INTEGER,
                addr VARCHAR,
                formula_raw VARCHAR,
                cached_value VARCHAR,
                hidden BOOLEAN,
                number_format VARCHAR,
                comment VARCHAR
            )
            """
        )
        if rows:
            con.executemany(
                "INSERT INTO cells VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [tuple(row[col] for col in _CELL_COLUMNS) for row in rows],
            )
        dest = tmp.as_posix().replace("'", "''")
        con.execute(f"COPY cells TO '{dest}' (FORMAT PARQUET)")
    finally:
        con.close()
    tmp.replace(path)
