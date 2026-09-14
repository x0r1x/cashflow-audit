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
    assert row.metrics.get("volume_change") is None
    assert row.metrics.get("price_change") is None
    assert row.metrics.get("fx_change") is None


def test_f01_drop_with_mapped_volume_price_decomposes() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="Revenue"),
            LayoutRow(row=3, label="Volume"),
            LayoutRow(row=4, label="Price"),
        ],
        axis=headers(
            (2, "2023", "historical"),
            (3, "2024E", "forecast"),
            (4, "2025E", "forecast"),
        ),
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "Revenue", "pnl.revenue", role="output"),
            mapped("P&L", 3, "Volume", "pnl.volume", role="assumption"),
            mapped("P&L", 4, "Price", "pnl.price", role="assumption"),
        ]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("P&L", "B2", "100"),
            cell("P&L", "C2", "110"),
            cell("P&L", "D2", "80"),
            cell("P&L", "B3", "10"),
            cell("P&L", "C3", "10"),
            cell("P&L", "D3", "8"),
            cell("P&L", "B4", "10"),
            cell("P&L", "C4", "11"),
            cell("P&L", "D4", "10"),
        ],
    )
    row = _control(doc, "F01")
    assert row.status == "flagged"
    assert row.metrics.get("volume_change") == (8 / 10) - 1.0
    assert row.metrics.get("price_change") == (10 / 11) - 1.0
    assert "P&L!C3" in row.cell_refs
    assert "P&L!D3" in row.cell_refs
    assert "P&L!C4" in row.cell_refs
    assert "P&L!D4" in row.cell_refs
    assert row.metrics.get("fx_change") is None


def test_f01_drop_with_mapped_fx_rate_reports_fx_change() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="Revenue"),
            LayoutRow(row=3, label="FX rate"),
        ],
        axis=headers(
            (2, "2023", "historical"),
            (3, "2024E", "forecast"),
            (4, "2025E", "forecast"),
        ),
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "Revenue", "pnl.revenue", role="output"),
            mapped("P&L", 3, "FX rate", "fx.rate", role="assumption"),
        ]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("P&L", "B2", "100"),
            cell("P&L", "C2", "110"),
            cell("P&L", "D2", "80"),
            cell("P&L", "B3", "80"),
            cell("P&L", "C3", "80"),
            cell("P&L", "D3", "72"),
        ],
    )
    row = _control(doc, "F01")
    assert row.status == "flagged"
    assert row.metrics.get("fx_change") == (72 / 80) - 1.0
    assert "P&L!C3" in row.cell_refs
    assert "P&L!D3" in row.cell_refs


def test_f01_plan_fact_gap_is_flagged() -> None:
    layout = simple_layout(
        [LayoutRow(row=2, label="Revenue")],
        axis=headers(
            (2, "2023", "historical"),
            (3, "2024", "historical"),
            (4, "2024", "forecast"),
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
            cell("P&L", "C2", "100"),
            cell("P&L", "D2", "80"),
        ],
    )
    row = _control(doc, "F01")
    assert row.status == "flagged"
    assert "P&L!C2" in row.cell_refs
    assert "P&L!D2" in row.cell_refs


def test_f01_plan_fact_within_band_is_clear() -> None:
    layout = simple_layout(
        [LayoutRow(row=2, label="Revenue")],
        axis=headers(
            (2, "2024", "historical"),
            (3, "2024", "forecast"),
        ),
    )
    mapping = MappingDocument(
        rows=[mapped("P&L", 2, "Revenue", "pnl.revenue", role="output")]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[cell("P&L", "B2", "100"), cell("P&L", "C2", "105")],
    )
    assert _control(doc, "F01").status == "clear"


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
