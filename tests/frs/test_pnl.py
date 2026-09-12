from __future__ import annotations

from tests.checkers.conftest import cell, headers, mapped, simple_layout

from cashflow_audit.frs.router import run_frs
from cashflow_audit.layout.models import LayoutRow
from cashflow_audit.mapping.models import MappingDocument


def _control(doc, fid: str):
    return next(c for c in doc.controls if c.id == fid)


def test_f01_forecast_revenue_drop_is_flagged() -> None:
    layout = simple_layout(
        [LayoutRow(row=2, label="Revenue")],
        axis=headers(
            (2, "2023", "historical"),
            (3, "2024E", "forecast"),
            (4, "2025E", "forecast"),
        ),
    )
    mapping = MappingDocument(
        rows=[mapped("P&L", 2, "Revenue", "pnl.revenue", role="output")]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("P&L", "B2", "100"),
            cell("P&L", "C2", "110"),
            cell("P&L", "D2", "80"),
        ],
    )
    row = _control(doc, "F01")
    assert row.status == "flagged"
    assert any(i.control_id == "F01" for i in doc.issues)


def test_f01_growth_is_clear() -> None:
    layout = simple_layout([LayoutRow(row=2, label="Revenue")])
    mapping = MappingDocument(
        rows=[mapped("P&L", 2, "Revenue", "pnl.revenue", role="output")]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[cell("P&L", "B2", "100"), cell("P&L", "C2", "110")],
    )
    assert _control(doc, "F01").status == "clear"
    assert not any(i.control_id == "F01" for i in doc.issues)


def test_f03_two_negative_forecast_periods_flagged() -> None:
    layout = simple_layout(
        [LayoutRow(row=2, label="EBITDA")],
        axis=headers(
            (2, "2023", "historical"),
            (3, "2024E", "forecast"),
            (4, "2025E", "forecast"),
        ),
    )
    mapping = MappingDocument(
        rows=[mapped("P&L", 2, "EBITDA", "pnl.ebitda", role="output")]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("P&L", "B2", "10"),
            cell("P&L", "C2", "-1"),
            cell("P&L", "D2", "-2"),
        ],
    )
    assert _control(doc, "F03").status == "flagged"


def test_f11_forecast_above_history_is_flagged() -> None:
    layout = simple_layout(
        [LayoutRow(row=2, label="Revenue")],
        axis=headers(
            (2, "2022", "historical"),
            (3, "2023", "historical"),
            (4, "2024E", "forecast"),
            (5, "2025E", "forecast"),
        ),
    )
    mapping = MappingDocument(
        rows=[mapped("P&L", 2, "Revenue", "pnl.revenue", role="output")]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("P&L", "B2", "100"),
            cell("P&L", "C2", "105"),
            cell("P&L", "D2", "130"),
            cell("P&L", "E2", "160"),
        ],
    )
    assert _control(doc, "F11").status == "flagged"
