from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb


class IrCatalog:
    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self._con = connection

    @classmethod
    def open(cls, audit_dir: Path) -> IrCatalog:
        con = duckdb.connect(":memory:")
        cells = _sql_path(audit_dir / "ir" / "cells.parquet")
        edges = _sql_path(audit_dir / "ir" / "edges.parquet")
        con.execute(f"CREATE VIEW cells AS SELECT * FROM read_parquet('{cells}')")
        con.execute(f"CREATE VIEW edges AS SELECT * FROM read_parquet('{edges}')")
        return cls(con)

    def sql(self, query: str) -> Any:
        return self._con.execute(query)

    def close(self) -> None:
        self._con.close()


def _sql_path(path: Path) -> str:
    return path.as_posix().replace("'", "''")
