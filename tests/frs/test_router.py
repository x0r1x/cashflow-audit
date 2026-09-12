from __future__ import annotations

from cashflow_audit.frs.router import run_frs
from cashflow_audit.mapping.models import MappingDocument


def test_empty_mapping_is_fourteen_not_applicable() -> None:
    doc = run_frs(MappingDocument(rows=[]))
    assert [c.id for c in doc.controls] == [f"F{i:02d}" for i in range(1, 15)]
    assert {c.status for c in doc.controls} == {"not_applicable"}
    assert doc.issues == []
