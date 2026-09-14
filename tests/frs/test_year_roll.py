from __future__ import annotations

from tests.checkers.conftest import cell, headers, mapped, simple_layout

from cashflow_audit.frs.router import run_frs
from cashflow_audit.layout.models import Layout, LayoutRow
from cashflow_audit.mapping.models import MappingDocument

TWO_YEARS = headers(
    (2, "2024-01", "historical"),
    (3, "2024-02", "historical"),
    (4, "2025-01", "forecast"),
    (5, "2025-02", "forecast"),
)


def _control(doc, fid: str):
    return next(c for c in doc.controls if c.id == fid)


def _stack(*layouts: Layout) -> Layout:
    return Layout(sheets=[sheet for layout in layouts for sheet in layout.sheets])


def test_f01_sums_months_into_calendar_year() -> None:
    layout = simple_layout([LayoutRow(row=2, label="Revenue")], axis=TWO_YEARS)
    mapping = MappingDocument(
        rows=[mapped("P&L", 2, "Revenue", "pnl.revenue", role="output")]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("P&L", "B2", "90"),
            cell("P&L", "C2", "10"),
            cell("P&L", "D2", "50"),
            cell("P&L", "E2", "12"),
        ],
    )
    row = _control(doc, "F01")
    assert row.status == "flagged"
    assert row.metrics.get("period_key") == "2025"
    assert row.confidence == "medium"


def test_f06_uses_last_month_stock_not_sum() -> None:
    layout = _stack(
        simple_layout([LayoutRow(row=2, label="Debt")], sheet="BS", axis=TWO_YEARS),
        simple_layout([LayoutRow(row=2, label="EBITDA")], sheet="P&L", axis=TWO_YEARS),
    )
    mapping = MappingDocument(
        rows=[
            mapped("BS", 2, "Debt", "bs.debt", role="output"),
            mapped("P&L", 2, "EBITDA", "pnl.ebitda", role="output"),
        ]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("BS", "B2", "80"),
            cell("BS", "C2", "100"),
            cell("BS", "D2", "90"),
            cell("BS", "E2", "120"),
            cell("P&L", "B2", "40"),
            cell("P&L", "C2", "10"),
            cell("P&L", "D2", "40"),
            cell("P&L", "E2", "10"),
        ],
    )
    row = _control(doc, "F06")
    assert row.status == "clear"
    assert row.confidence == "medium"
    assert row.metrics.get("ratio") == 120 / 50
    assert row.metrics.get("prev_ratio") == 100 / 50
    assert row.metrics.get("period_key") == "2025"


def test_f04_accruals_are_ni_minus_cfo() -> None:
    years = headers(
        (2, "2023", "historical"),
        (3, "2024E", "forecast"),
        (4, "2025E", "forecast"),
    )
    layout = _stack(
        simple_layout([LayoutRow(row=2, label="NI")], sheet="P&L", axis=years),
        simple_layout(
            [LayoutRow(row=2, label="CFO"), LayoutRow(row=3, label="CAPEX")],
            sheet="CF",
            axis=years,
        ),
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "NI", "pnl.net_income", role="output"),
            mapped("CF", 2, "CFO", "cf.cfo", role="output"),
            mapped("CF", 3, "CAPEX", "cf.capex", role="calculation"),
        ]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("P&L", "B2", "10"),
            cell("P&L", "C2", "12"),
            cell("P&L", "D2", "14"),
            cell("CF", "B2", "9"),
            cell("CF", "C2", "8"),
            cell("CF", "D2", "8"),
            cell("CF", "B3", "-1"),
            cell("CF", "C3", "-12"),
            cell("CF", "D3", "-14"),
        ],
    )
    row = _control(doc, "F04")
    assert row.status == "flagged"
    assert row.metrics.get("accruals") == 4.0
    assert row.metrics.get("ni") == 12.0
    assert row.metrics.get("cfo") == 8.0


QUARTERS = headers(
    (2, "2024Q1", "historical"),
    (3, "2024Q2", "historical"),
    (4, "2024Q3", "historical"),
    (5, "2024Q4", "historical"),
    (6, "2025Q1", "forecast"),
    (7, "2025Q2", "forecast"),
    (8, "2025Q3", "forecast"),
    (9, "2025Q4", "forecast"),
)


def test_f01_sums_complete_quarters_into_calendar_year() -> None:
    layout = simple_layout([LayoutRow(row=2, label="Revenue")], axis=QUARTERS)
    mapping = MappingDocument(
        rows=[mapped("P&L", 2, "Revenue", "pnl.revenue", role="output")]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("P&L", "B2", "25"),
            cell("P&L", "C2", "25"),
            cell("P&L", "D2", "25"),
            cell("P&L", "E2", "25"),
            cell("P&L", "F2", "10"),
            cell("P&L", "G2", "10"),
            cell("P&L", "H2", "10"),
            cell("P&L", "I2", "10"),
        ],
    )
    row = _control(doc, "F01")
    assert row.status == "flagged"
    assert row.metrics.get("period_key") == "2025"


def test_f01_incomplete_quarters_are_not_annual_evidence() -> None:
    layout = simple_layout(
        [LayoutRow(row=2, label="Revenue")],
        axis=headers(
            (2, "2024Q1", "historical"),
            (3, "2024Q2", "historical"),
            (4, "2024Q3", "historical"),
            (5, "2025Q1", "forecast"),
            (6, "2025Q2", "forecast"),
        ),
    )
    mapping = MappingDocument(
        rows=[mapped("P&L", 2, "Revenue", "pnl.revenue", role="output")]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("P&L", "B2", "90"),
            cell("P&L", "C2", "10"),
            cell("P&L", "D2", "10"),
            cell("P&L", "E2", "50"),
            cell("P&L", "F2", "12"),
        ],
    )
    assert _control(doc, "F01").status != "flagged"


def test_f06_uses_q4_stock_not_sum() -> None:
    layout = _stack(
        simple_layout([LayoutRow(row=2, label="Debt")], sheet="BS", axis=QUARTERS),
        simple_layout([LayoutRow(row=2, label="EBITDA")], sheet="P&L", axis=QUARTERS),
    )
    mapping = MappingDocument(
        rows=[
            mapped("BS", 2, "Debt", "bs.debt", role="output"),
            mapped("P&L", 2, "EBITDA", "pnl.ebitda", role="output"),
        ]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("BS", "B2", "10"),
            cell("BS", "C2", "20"),
            cell("BS", "D2", "30"),
            cell("BS", "E2", "100"),
            cell("BS", "F2", "40"),
            cell("BS", "G2", "50"),
            cell("BS", "H2", "60"),
            cell("BS", "I2", "120"),
            cell("P&L", "B2", "10"),
            cell("P&L", "C2", "10"),
            cell("P&L", "D2", "10"),
            cell("P&L", "E2", "20"),
            cell("P&L", "F2", "10"),
            cell("P&L", "G2", "10"),
            cell("P&L", "H2", "10"),
            cell("P&L", "I2", "20"),
        ],
    )
    row = _control(doc, "F06")
    assert row.status == "clear"
    assert row.metrics.get("ratio") == 120 / 50
    assert row.metrics.get("prev_ratio") == 100 / 50
