from __future__ import annotations

from cashflow_audit.checkers.models import Candidate
from cashflow_audit.compile.csr import build_csr
from cashflow_audit.compile.models import Edge
from cashflow_audit.lineage.trace import trace_lineage
from cashflow_audit.mapping.models import MappedRow, MappingDocument


def _map(sheet: str, row: int, concept_id: str, role: str = "output") -> MappedRow:
    return MappedRow(
        row_key=f"{sheet}|{row}|{sheet}!r1",
        sheet=sheet,
        row=row,
        block_id=f"{sheet}!r1",
        label=concept_id,
        concept_id=concept_id,
        article_role=role,  # type: ignore[arg-type]
        source="glossary",
    )


def test_reverse_bfs_reaches_output_concept_metrics() -> None:
    # In!A1 <- P&L!B2 <- P&L!C3 (output ebitda)
    csr = build_csr(
        [
            Edge(kind="ref", source="P&L!B2", target="In!A1"),
            Edge(kind="ref", source="P&L!C3", target="P&L!B2"),
        ],
        extra_nodes=["In!A1", "P&L!B2", "P&L!C3"],
    )
    mapping = MappingDocument(
        rows=[
            _map("P&L", 3, "pnl.ebitda", "output"),
            _map("P&L", 2, "pnl.revenue", "calculation"),
        ]
    )
    cand = Candidate(detector="excel_error", cell_refs=["In!A1"], base_severity="error")
    doc = trace_lineage([cand], csr=csr, mapping=mapping)
    item = doc.items[0]
    assert "pnl.ebitda" in item.affected_metrics
    assert "P&L!C3" in item.output_refs
    assert "pnl.revenue" not in item.affected_metrics
    assert item.impact.kind == "direction"
    assert item.impact.value == "downstream"


def test_identity_delta_is_numeric_impact() -> None:
    csr = build_csr([], extra_nodes=["BS!B2", "BS!B3", "BS!B4"])
    mapping = MappingDocument(
        rows=[
            _map("BS", 2, "bs.assets_total"),
            _map("BS", 3, "bs.equity"),
            _map("BS", 4, "bs.liabilities"),
        ]
    )
    cand = Candidate(
        detector="identity.I1",
        cell_refs=["BS!B2", "BS!B3", "BS!B4"],
        payload={"delta": 1200.0},
        base_severity="error",
    )
    item = trace_lineage([cand], csr=csr, mapping=mapping).items[0]
    assert item.impact.kind == "numeric"
    assert item.impact.value == 1200.0
    assert set(item.affected_metrics) == {
        "bs.assets_total",
        "bs.equity",
        "bs.liabilities",
    }


def test_truncated_range_is_not_complete() -> None:
    csr = build_csr(
        [Edge(kind="ref", source="P&L!C3", target="In!A1")],
        extra_nodes=["In!A1", "P&L!C3"],
    )
    csr.truncated_sources.add("P&L!C3")
    mapping = MappingDocument(rows=[_map("P&L", 3, "pnl.ebitda")])
    cand = Candidate(detector="excel_error", cell_refs=["In!A1"])
    item = trace_lineage([cand], csr=csr, mapping=mapping).items[0]
    assert item.truncated is True
    assert item.complete is False


def test_metrics_come_only_from_lineage_not_detector_name() -> None:
    csr = build_csr(
        [Edge(kind="ref", source="P&L!C3", target="In!A1")],
        extra_nodes=["In!A1", "P&L!C3"],
    )
    mapping = MappingDocument(rows=[_map("P&L", 3, "pnl.ebitda")])
    cand = Candidate(detector="hardcode_in_formula", cell_refs=["In!A1"])
    item = trace_lineage([cand], csr=csr, mapping=mapping).items[0]
    assert item.affected_metrics == ["pnl.ebitda"]
    assert "hardcode" not in "".join(item.affected_metrics)
