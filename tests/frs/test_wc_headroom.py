from __future__ import annotations

from tests.checkers.conftest import cell, headers, mapped, simple_layout

from cashflow_audit.frs.router import run_frs
from cashflow_audit.layout.models import Layout, LayoutRow
from cashflow_audit.mapping.models import MappingDocument

YEARS = headers(
    (2, "2023", "historical"),
    (3, "2024E", "forecast"),
    (4, "2025E", "forecast"),
)


def _control(doc, fid: str):
    return next(c for c in doc.controls if c.id == fid)


def _issue(doc, fid: str):
    return next(i for i in doc.issues if i.control_id == fid)


def _stack(*layouts: Layout) -> Layout:
    return Layout(sheets=[sheet for layout in layouts for sheet in layout.sheets])


def test_f05_one_wc_line_is_not_applicable() -> None:
    layout = _stack(
        simple_layout([LayoutRow(row=2, label="AR")], sheet="BS", axis=YEARS),
        simple_layout([LayoutRow(row=2, label="Rev")], sheet="P&L", axis=YEARS),
    )
    mapping = MappingDocument(
        rows=[
            mapped("BS", 2, "AR", "bs.ar", role="output"),
            mapped("P&L", 2, "Rev", "pnl.revenue", role="output"),
        ]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("BS", "B2", "100"),
            cell("BS", "C2", "250"),
            cell("BS", "D2", "250"),
            cell("P&L", "B2", "3650"),
            cell("P&L", "C2", "3650"),
            cell("P&L", "D2", "3650"),
        ],
    )
    assert _control(doc, "F05").status == "not_applicable"
    assert not any(i.control_id == "F05" for i in doc.issues)


def test_f05_dso_lift_is_flagged() -> None:
    layout = _stack(
        simple_layout(
            [LayoutRow(row=2, label="AR"), LayoutRow(row=3, label="AP")],
            sheet="BS",
            axis=YEARS,
        ),
        simple_layout([LayoutRow(row=2, label="Rev")], sheet="P&L", axis=YEARS),
    )
    mapping = MappingDocument(
        rows=[
            mapped("BS", 2, "AR", "bs.ar", role="output"),
            mapped("BS", 3, "AP", "bs.ap", role="output"),
            mapped("P&L", 2, "Rev", "pnl.revenue", role="output"),
        ]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("BS", "B2", "100"),
            cell("BS", "C2", "250"),
            cell("BS", "D2", "250"),
            cell("BS", "B3", "50"),
            cell("BS", "C3", "50"),
            cell("BS", "D3", "50"),
            cell("P&L", "B2", "3650"),
            cell("P&L", "C2", "3650"),
            cell("P&L", "D2", "3650"),
        ],
    )
    row = _control(doc, "F05")
    assert row.status == "flagged"
    assert row.metrics.get("dso") == 25.0
    issue = _issue(doc, "F05")
    assert issue.class_name == "cash_conversion"
    assert issue.priority == "medium"


def test_f05_ar_grows_faster_than_revenue() -> None:
    layout = _stack(
        simple_layout(
            [LayoutRow(row=2, label="AR"), LayoutRow(row=3, label="Inv")],
            sheet="BS",
            axis=YEARS,
        ),
        simple_layout([LayoutRow(row=2, label="Rev")], sheet="P&L", axis=YEARS),
    )
    mapping = MappingDocument(
        rows=[
            mapped("BS", 2, "AR", "bs.ar", role="output"),
            mapped("BS", 3, "Inv", "bs.inventory", role="output"),
            mapped("P&L", 2, "Rev", "pnl.revenue", role="output"),
        ]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("BS", "B2", "100"),
            cell("BS", "C2", "200"),
            cell("BS", "D2", "200"),
            cell("BS", "B3", "40"),
            cell("BS", "C3", "40"),
            cell("BS", "D3", "40"),
            cell("P&L", "B2", "1000"),
            cell("P&L", "C2", "1100"),
            cell("P&L", "D2", "1100"),
        ],
    )
    assert _control(doc, "F05").status == "flagged"


def test_f05_dpo_from_cogs_not_revenue() -> None:
    layout = _stack(
        simple_layout(
            [LayoutRow(row=2, label="AR"), LayoutRow(row=3, label="AP")],
            sheet="BS",
            axis=YEARS,
        ),
        simple_layout(
            [LayoutRow(row=2, label="Rev"), LayoutRow(row=3, label="COGS")],
            sheet="P&L",
            axis=YEARS,
        ),
    )
    mapping = MappingDocument(
        rows=[
            mapped("BS", 2, "AR", "bs.ar", role="output"),
            mapped("BS", 3, "AP", "bs.ap", role="output"),
            mapped("P&L", 2, "Rev", "pnl.revenue", role="output"),
            mapped("P&L", 3, "COGS", "pnl.cogs", role="calculation"),
        ]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("BS", "B2", "10"),
            cell("BS", "C2", "10"),
            cell("BS", "D2", "10"),
            cell("BS", "B3", "100"),
            cell("BS", "C3", "250"),
            cell("BS", "D3", "250"),
            cell("P&L", "B2", "36500"),
            cell("P&L", "C2", "36500"),
            cell("P&L", "D2", "36500"),
            cell("P&L", "B3", "3650"),
            cell("P&L", "C3", "3650"),
            cell("P&L", "D3", "3650"),
        ],
    )
    row = _control(doc, "F05")
    assert row.status == "flagged"
    assert row.metrics.get("dpo") == 25.0


def test_f05_without_cogs_skips_dio_dpo() -> None:
    layout = _stack(
        simple_layout(
            [LayoutRow(row=2, label="AR"), LayoutRow(row=3, label="Inv")],
            sheet="BS",
            axis=YEARS,
        ),
        simple_layout([LayoutRow(row=2, label="Rev")], sheet="P&L", axis=YEARS),
    )
    mapping = MappingDocument(
        rows=[
            mapped("BS", 2, "AR", "bs.ar", role="output"),
            mapped("BS", 3, "Inv", "bs.inventory", role="output"),
            mapped("P&L", 2, "Rev", "pnl.revenue", role="output"),
        ]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("BS", "B2", "100"),
            cell("BS", "C2", "100"),
            cell("BS", "D2", "100"),
            cell("BS", "B3", "40"),
            cell("BS", "C3", "40"),
            cell("BS", "D3", "40"),
            cell("P&L", "B2", "3650"),
            cell("P&L", "C2", "3650"),
            cell("P&L", "D2", "3650"),
        ],
    )
    row = _control(doc, "F05")
    assert row.status == "clear"
    assert row.metrics.get("dio") is None
    assert row.metrics.get("dpo") is None


def test_f09_year_axis_is_insufficient() -> None:
    layout = simple_layout(
        [LayoutRow(row=2, label="Repay")], sheet="CF", axis=YEARS
    )
    mapping = MappingDocument(
        rows=[mapped("CF", 2, "Repay", "cf.repayment", role="calculation")]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("CF", "B2", "10"),
            cell("CF", "C2", "80"),
            cell("CF", "D2", "10"),
        ],
    )
    assert _control(doc, "F09").status == "insufficient"
    assert not any(i.control_id == "F09" for i in doc.issues)


def test_f09_balloon_year_is_flagged() -> None:
    months = headers(
        (2, "2025-01", "forecast"),
        (3, "2025-02", "forecast"),
        (4, "2026-01", "forecast"),
        (5, "2026-02", "forecast"),
        (6, "2027-01", "forecast"),
        (7, "2027-02", "forecast"),
    )
    layout = simple_layout(
        [LayoutRow(row=2, label="Repay")], sheet="CF", axis=months
    )
    mapping = MappingDocument(
        rows=[mapped("CF", 2, "Repay", "cf.repayment", role="calculation")]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("CF", "B2", "10"),
            cell("CF", "C2", "10"),
            cell("CF", "D2", "10"),
            cell("CF", "E2", "10"),
            cell("CF", "F2", "80"),
            cell("CF", "G2", "80"),
        ],
    )
    row = _control(doc, "F09")
    assert row.status == "flagged"
    assert row.metrics.get("year") == "2027"
    assert row.metrics.get("peak_month") == "2027-01"
    issue = _issue(doc, "F09")
    assert issue.class_name == "refinancing"
    assert issue.priority == "medium"


def test_f09_even_repayments_are_clear() -> None:
    months = headers(
        (2, "2025-01", "forecast"),
        (3, "2026-01", "forecast"),
        (4, "2027-01", "forecast"),
        (5, "2028-01", "forecast"),
    )
    layout = simple_layout(
        [LayoutRow(row=2, label="Repay")], sheet="CF", axis=months
    )
    mapping = MappingDocument(
        rows=[mapped("CF", 2, "Repay", "cf.repayment", role="calculation")]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("CF", "B2", "10"),
            cell("CF", "C2", "10"),
            cell("CF", "D2", "10"),
            cell("CF", "E2", "10"),
        ],
    )
    assert _control(doc, "F09").status == "clear"


def test_f10_without_fx_is_insufficient() -> None:
    layout = simple_layout([LayoutRow(row=2, label="Interest")], axis=YEARS)
    mapping = MappingDocument(
        rows=[mapped("P&L", 2, "Interest", "pnl.interest", role="calculation")]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("P&L", "B2", "5"),
            cell("P&L", "C2", "8"),
            cell("P&L", "D2", "12"),
        ],
    )
    row = _control(doc, "F10")
    assert row.status == "insufficient"
    assert "fx" in row.metrics
    assert row.metrics["fx"] is None
    assert not any(i.control_id == "F10" for i in doc.issues)


def test_f12_revenue_up_fcf_down_is_flagged() -> None:
    layout = _stack(
        simple_layout([LayoutRow(row=2, label="Rev")], sheet="P&L", axis=YEARS),
        simple_layout(
            [LayoutRow(row=2, label="FCF"), LayoutRow(row=3, label="CAPEX")],
            sheet="CF",
            axis=YEARS,
        ),
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "Rev", "pnl.revenue", role="output"),
            mapped("CF", 2, "FCF", "cf.fcf", role="output"),
            mapped("CF", 3, "CAPEX", "cf.capex", role="calculation"),
        ]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("P&L", "B2", "100"),
            cell("P&L", "C2", "120"),
            cell("P&L", "D2", "180"),
            cell("CF", "B2", "10"),
            cell("CF", "C2", "8"),
            cell("CF", "D2", "-5"),
            cell("CF", "B3", "-4"),
            cell("CF", "C3", "-8"),
            cell("CF", "D3", "-20"),
        ],
    )
    row = _control(doc, "F12")
    assert row.status == "flagged"
    issue = _issue(doc, "F12")
    assert issue.class_name == "economic_logic"
    assert issue.priority == "medium"


def test_f12_without_fcf_is_insufficient() -> None:
    layout = simple_layout([LayoutRow(row=2, label="Rev")], axis=YEARS)
    mapping = MappingDocument(
        rows=[mapped("P&L", 2, "Rev", "pnl.revenue", role="output")]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("P&L", "B2", "100"),
            cell("P&L", "C2", "150"),
            cell("P&L", "D2", "200"),
        ],
    )
    assert _control(doc, "F12").status == "insufficient"
    assert not any(i.control_id == "F12" for i in doc.issues)


def test_f14_without_covenants_is_insufficient() -> None:
    layout = simple_layout(
        [LayoutRow(row=2, label="Cash")], sheet="BS", axis=YEARS
    )
    mapping = MappingDocument(
        rows=[mapped("BS", 2, "Cash", "bs.cash", role="output")]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("BS", "B2", "80"),
            cell("BS", "C2", "40"),
            cell("BS", "D2", "60"),
        ],
    )
    row = _control(doc, "F14")
    assert row.status == "insufficient"
    assert row.metrics.get("min_cash") == 40.0
    assert not any(i.control_id == "F14" for i in doc.issues)
