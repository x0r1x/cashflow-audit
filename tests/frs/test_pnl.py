from __future__ import annotations

from tests.checkers.conftest import cell, headers, mapped, simple_layout

from cashflow_audit.frs.router import run_frs
from cashflow_audit.layout.detect import detect_layout
from cashflow_audit.layout.models import AxisHeader, LayoutRow
from cashflow_audit.layout.periods import classify_header
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


def _classified(*cols: tuple[int, str]) -> list[AxisHeader]:
    found: list[AxisHeader] = []
    for col, text in cols:
        hit = classify_header(text)
        assert hit is not None
        found.append(
            AxisHeader(col=col, text=text, role=hit.role, period_key=hit.period_key)
        )
    return found


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


def test_f01_plain_year_vs_e_suffix_is_plan_fact() -> None:
    layout = simple_layout(
        [LayoutRow(row=2, label="Revenue")],
        axis=_classified((2, "2024"), (3, "2024E")),
    )
    mapping = MappingDocument(
        rows=[mapped("P&L", 2, "Revenue", "pnl.revenue", role="output")]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[cell("P&L", "B2", "100"), cell("P&L", "C2", "80")],
    )
    row = _control(doc, "F01")
    assert row.status == "flagged"
    assert row.metrics.get("period_key") == "2024"
    assert "P&L!B2" in row.cell_refs
    assert "P&L!C2" in row.cell_refs


def test_f01_hist_year_vs_next_forecast_is_not_plan_fact() -> None:
    layout = simple_layout(
        [LayoutRow(row=2, label="Revenue")],
        axis=_classified((2, "2023"), (3, "2024E")),
    )
    mapping = MappingDocument(
        rows=[mapped("P&L", 2, "Revenue", "pnl.revenue", role="output")]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[cell("P&L", "B2", "100"), cell("P&L", "C2", "95")],
    )
    assert _control(doc, "F01").status == "clear"
    assert not any(i.control_id == "F01" for i in doc.issues)


def test_f01_month_plan_fact_gap_is_flagged() -> None:
    layout = simple_layout(
        [LayoutRow(row=2, label="Revenue")],
        axis=_classified((2, "янв.25 факт"), (3, "янв.25 план")),
    )
    mapping = MappingDocument(
        rows=[mapped("P&L", 2, "Revenue", "pnl.revenue", role="output")]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[cell("P&L", "B2", "100"), cell("P&L", "C2", "80")],
    )
    row = _control(doc, "F01")
    assert row.status == "flagged"
    assert row.metrics.get("period_key") == "2025-01"


def test_f01_plan_fact_from_detected_layout() -> None:
    cells = [
        cell("P&L", "A1", "Item"),
        cell("P&L", "B1", "2024"),
        cell("P&L", "C1", "2024E"),
        cell("P&L", "A2", "Revenue"),
        cell("P&L", "B2", "100"),
        cell("P&L", "C2", "80"),
    ]
    layout = detect_layout(cells)
    mapping = MappingDocument(
        rows=[mapped("P&L", 2, "Revenue", "pnl.revenue", role="output")]
    )
    doc = run_frs(mapping, layout=layout, cells=cells)
    assert _control(doc, "F01").status == "flagged"


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


def test_f02_clear_keeps_forecast_margin() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="Revenue"),
            LayoutRow(row=3, label="EBITDA"),
        ]
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "Revenue", "pnl.revenue", role="output"),
            mapped("P&L", 3, "EBITDA", "pnl.ebitda", role="output"),
        ]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("P&L", "B2", "100"),
            cell("P&L", "C2", "110"),
            cell("P&L", "B3", "50"),
            cell("P&L", "C3", "55"),
        ],
    )
    row = _control(doc, "F02")
    assert row.status == "clear"
    assert row.metrics.get("margin") == 55 / 110


def test_f02_margin_drop_is_flagged() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="Revenue"),
            LayoutRow(row=3, label="EBITDA"),
        ]
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "Revenue", "pnl.revenue", role="output"),
            mapped("P&L", 3, "EBITDA", "pnl.ebitda", role="output"),
        ]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("P&L", "B2", "100"),
            cell("P&L", "C2", "120"),
            cell("P&L", "B3", "50"),
            cell("P&L", "C3", "48"),
        ],
    )
    row = _control(doc, "F02")
    assert row.status == "flagged"
    assert row.metrics.get("margin") == 48 / 120
    assert row.metrics.get("margin_drop") == 48 / 120 - 50 / 100


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
