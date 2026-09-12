from __future__ import annotations

from cashflow_audit.checkers.run import run_checks
from cashflow_audit.layout.models import LayoutRow
from cashflow_audit.mapping.models import MappingDocument
from tests.checkers.conftest import cell, ctx, mapped, simple_layout


def test_check_does_not_emit_risk_detectors() -> None:
    layout = simple_layout([LayoutRow(row=2, label="Revenue")])
    mapping = MappingDocument(
        rows=[mapped("P&L", 2, "Revenue", "pnl.revenue", role="output")]
    )
    result = run_checks(
        ctx(
            [cell("P&L", "B2", "100"), cell("P&L", "C2", "85")],
            layout=layout,
            mapping=mapping,
        )
    )
    assert not any(c.detector.startswith("risk.") for c in result.candidates)
