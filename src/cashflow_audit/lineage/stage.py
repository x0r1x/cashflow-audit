from __future__ import annotations

from pathlib import Path

from cashflow_audit.checkers.models import CheckDocument
from cashflow_audit.compile.csr import build_csr
from cashflow_audit.compile.models import Edge
from cashflow_audit.lineage.models import LineageDocument
from cashflow_audit.lineage.trace import trace_lineage
from cashflow_audit.mapping.models import MappingDocument
from cashflow_audit.store.fs import read_parquet, write_json


def lineage_workbook(dest_dir: Path) -> LineageDocument:
    path = dest_dir / "lineage.json"
    if path.exists():
        return LineageDocument.model_validate_json(path.read_text(encoding="utf-8"))
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
    doc = trace_lineage(check.candidates, csr=csr, mapping=mapping)
    write_json(path, doc.model_dump(mode="json"))
    return doc
