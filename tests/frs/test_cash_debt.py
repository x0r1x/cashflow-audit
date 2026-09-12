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
MONTHS = headers(
    (2, "2025-01", "forecast"),
    (3, "2025-02", "forecast"),
    (4, "2025-03", "forecast"),
)


def _control(doc, fid: str):
    return next(c for c in doc.controls if c.id == fid)


def _issue(doc, fid: str):
    return next(i for i in doc.issues if i.control_id == fid)


def _stack(*layouts: Layout) -> Layout:
    return Layout(sheets=[sheet for layout in layouts for sheet in layout.sheets])


def test_f04_negative_fcf_with_profit_is_flagged() -> None:
    layout = _stack(
        simple_layout([LayoutRow(row=2, label="NI")], sheet="P&L", axis=YEARS),
        simple_layout([LayoutRow(row=2, label="FCF")], sheet="CF", axis=YEARS),
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "NI", "pnl.net_income", role="output"),
            mapped("CF", 2, "FCF", "cf.fcf", role="output"),
        ]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("P&L", "B2", "10"),
            cell("P&L", "C2", "12"),
            cell("P&L", "D2", "14"),
            cell("CF", "B2", "5"),
            cell("CF", "C2", "-2"),
            cell("CF", "D2", "-3"),
        ],
    )
    row = _control(doc, "F04")
    assert row.status == "flagged"
    issue = _issue(doc, "F04")
    assert issue.priority == "medium"
    assert issue.class_name == "cash_conversion"
    assert "CF!C2" in row.cell_refs
    assert "CF!D2" in row.cell_refs


def test_f04_without_fcf_is_insufficient() -> None:
    layout = simple_layout([LayoutRow(row=2, label="NI")], axis=YEARS)
    mapping = MappingDocument(
        rows=[mapped("P&L", 2, "NI", "pnl.net_income", role="output")]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("P&L", "B2", "10"),
            cell("P&L", "C2", "12"),
            cell("P&L", "D2", "14"),
        ],
    )
    assert _control(doc, "F04").status == "insufficient"
    assert not any(i.control_id == "F04" for i in doc.issues)


def test_f04_derived_fcf_from_cfo_plus_capex() -> None:
    layout = _stack(
        simple_layout([LayoutRow(row=2, label="NI")], sheet="P&L", axis=YEARS),
        simple_layout(
            [LayoutRow(row=2, label="CFO"), LayoutRow(row=3, label="CAPEX")],
            sheet="CF",
            axis=YEARS,
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
    assert row.metrics.get("fcf_source") == "derived"
    assert _issue(doc, "F04").cause == "capex"


def test_f04_cfo_up_fcf_down_is_flagged() -> None:
    layout = _stack(
        simple_layout([LayoutRow(row=2, label="NI")], sheet="P&L", axis=YEARS),
        simple_layout(
            [LayoutRow(row=2, label="CFO"), LayoutRow(row=3, label="FCF")],
            sheet="CF",
            axis=YEARS,
        ),
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "NI", "pnl.net_income", role="output"),
            mapped("CF", 2, "CFO", "cf.cfo", role="output"),
            mapped("CF", 3, "FCF", "cf.fcf", role="output"),
        ]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("P&L", "B2", "10"),
            cell("P&L", "C2", "11"),
            cell("P&L", "D2", "12"),
            cell("CF", "B2", "8"),
            cell("CF", "C2", "10"),
            cell("CF", "D2", "13"),
            cell("CF", "B3", "7"),
            cell("CF", "C3", "5"),
            cell("CF", "D3", "2"),
        ],
    )
    assert _control(doc, "F04").status == "flagged"


def test_f04_profit_and_fcf_positive_is_clear() -> None:
    layout = _stack(
        simple_layout([LayoutRow(row=2, label="NI")], sheet="P&L", axis=YEARS),
        simple_layout([LayoutRow(row=2, label="FCF")], sheet="CF", axis=YEARS),
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "NI", "pnl.net_income", role="output"),
            mapped("CF", 2, "FCF", "cf.fcf", role="output"),
        ]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("P&L", "B2", "10"),
            cell("P&L", "C2", "12"),
            cell("P&L", "D2", "14"),
            cell("CF", "B2", "8"),
            cell("CF", "C2", "9"),
            cell("CF", "D2", "11"),
        ],
    )
    assert _control(doc, "F04").status == "clear"
    assert not any(i.control_id == "F04" for i in doc.issues)


def test_f06_ratio_jump_is_flagged() -> None:
    layout = _stack(
        simple_layout([LayoutRow(row=2, label="Debt")], sheet="BS", axis=YEARS),
        simple_layout([LayoutRow(row=2, label="EBITDA")], sheet="P&L", axis=YEARS),
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
            cell("BS", "B2", "100"),
            cell("BS", "C2", "250"),
            cell("BS", "D2", "260"),
            cell("P&L", "B2", "100"),
            cell("P&L", "C2", "50"),
            cell("P&L", "D2", "50"),
        ],
    )
    row = _control(doc, "F06")
    assert row.status == "flagged"
    assert _issue(doc, "F06").class_name == "leverage"
    assert _issue(doc, "F06").priority == "medium"


def test_f06_debt_up_ratio_down_is_clear() -> None:
    layout = _stack(
        simple_layout([LayoutRow(row=2, label="Debt")], sheet="BS", axis=YEARS),
        simple_layout([LayoutRow(row=2, label="EBITDA")], sheet="P&L", axis=YEARS),
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
            cell("BS", "B2", "100"),
            cell("BS", "C2", "216"),
            cell("BS", "D2", "216"),
            cell("P&L", "B2", "58"),
            cell("P&L", "C2", "151"),
            cell("P&L", "D2", "160"),
        ],
    )
    assert _control(doc, "F06").status == "clear"


def test_f06_uses_net_debt_when_cash_mapped() -> None:
    layout = _stack(
        simple_layout(
            [LayoutRow(row=2, label="Debt"), LayoutRow(row=3, label="Cash")],
            sheet="BS",
            axis=YEARS,
        ),
        simple_layout([LayoutRow(row=2, label="EBITDA")], sheet="P&L", axis=YEARS),
    )
    mapping = MappingDocument(
        rows=[
            mapped("BS", 2, "Debt", "bs.debt", role="output"),
            mapped("BS", 3, "Cash", "bs.cash", role="output"),
            mapped("P&L", 2, "EBITDA", "pnl.ebitda", role="output"),
        ]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("BS", "B2", "300"),
            cell("BS", "C2", "300"),
            cell("BS", "D2", "300"),
            cell("BS", "B3", "200"),
            cell("BS", "C3", "0"),
            cell("BS", "D3", "0"),
            cell("P&L", "B2", "100"),
            cell("P&L", "C2", "100"),
            cell("P&L", "D2", "100"),
        ],
    )
    assert _control(doc, "F06").status == "flagged"


def test_f07_icr_below_floor_is_flagged() -> None:
    layout = simple_layout(
        [LayoutRow(row=2, label="EBITDA"), LayoutRow(row=3, label="Interest")],
        axis=YEARS,
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "EBITDA", "pnl.ebitda", role="output"),
            mapped("P&L", 3, "Interest", "pnl.interest", role="calculation"),
        ]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("P&L", "B2", "10"),
            cell("P&L", "C2", "10"),
            cell("P&L", "D2", "10"),
            cell("P&L", "B3", "8"),
            cell("P&L", "C3", "8"),
            cell("P&L", "D3", "8"),
        ],
    )
    row = _control(doc, "F07")
    assert row.status == "flagged"
    assert row.metrics.get("dscr") is None
    issue = _issue(doc, "F07")
    assert issue.priority == "medium"
    assert issue.class_name == "leverage"


def test_f07_uses_ebitda_not_ebit() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="EBITDA"),
            LayoutRow(row=3, label="Interest"),
            LayoutRow(row=4, label="EBIT"),
        ],
        axis=YEARS,
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "EBITDA", "pnl.ebitda", role="output"),
            mapped("P&L", 3, "Interest", "pnl.interest", role="calculation"),
            mapped("P&L", 4, "EBIT", "pnl.ebit", role="output"),
        ]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("P&L", "B2", "30"),
            cell("P&L", "C2", "30"),
            cell("P&L", "D2", "30"),
            cell("P&L", "B3", "10"),
            cell("P&L", "C3", "10"),
            cell("P&L", "D3", "10"),
            cell("P&L", "B4", "10"),
            cell("P&L", "C4", "10"),
            cell("P&L", "D4", "10"),
        ],
    )
    assert _control(doc, "F07").status == "clear"


def test_f08_year_axis_is_insufficient() -> None:
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
            cell("BS", "B2", "100"),
            cell("BS", "C2", "80"),
            cell("BS", "D2", "60"),
        ],
    )
    assert _control(doc, "F08").status == "insufficient"
    assert not any(i.control_id == "F08" for i in doc.issues)


def test_f08_negative_month_is_flagged() -> None:
    layout = simple_layout(
        [LayoutRow(row=2, label="Cash")], sheet="BS", axis=MONTHS
    )
    mapping = MappingDocument(
        rows=[mapped("BS", 2, "Cash", "bs.cash", role="output")]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("BS", "B2", "100"),
            cell("BS", "C2", "50"),
            cell("BS", "D2", "-5"),
        ],
    )
    row = _control(doc, "F08")
    assert row.status == "flagged"
    assert row.metrics.get("runway") == 2
    issue = _issue(doc, "F08")
    assert issue.priority == "high"
    assert issue.class_name == "liquidity"
    assert "BS!D2" in row.cell_refs


def test_f08_cash_plug_is_not_clear() -> None:
    layout = _stack(
        simple_layout([LayoutRow(row=2, label="Cash")], sheet="BS", axis=MONTHS),
        simple_layout([LayoutRow(row=2, label="Draw")], sheet="CF", axis=MONTHS),
    )
    mapping = MappingDocument(
        rows=[
            mapped("BS", 2, "Cash", "bs.cash", role="output"),
            mapped("CF", 2, "Draw", "cf.drawdown", role="calculation"),
        ]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("BS", "B2", "100"),
            cell("BS", "C2", "100"),
            cell("BS", "D2", "100"),
            cell("CF", "B2", "10"),
            cell("CF", "C2", "20"),
            cell("CF", "D2", "15"),
        ],
    )
    row = _control(doc, "F08")
    assert row.status == "flagged"
    assert row.metrics.get("cash_plug") is True
    assert _issue(doc, "F08").cause == "cash_plug"


def test_f13_dividends_while_fcf_red_is_high() -> None:
    layout = simple_layout(
        [LayoutRow(row=2, label="Div"), LayoutRow(row=3, label="FCF")],
        sheet="CF",
        axis=YEARS,
    )
    mapping = MappingDocument(
        rows=[
            mapped("CF", 2, "Div", "cf.dividends", role="calculation"),
            mapped("CF", 3, "FCF", "cf.fcf", role="output"),
        ]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("CF", "B2", "0"),
            cell("CF", "C2", "5"),
            cell("CF", "D2", "5"),
            cell("CF", "B3", "4"),
            cell("CF", "C3", "-10"),
            cell("CF", "D3", "-8"),
        ],
    )
    row = _control(doc, "F13")
    assert row.status == "flagged"
    issue = _issue(doc, "F13")
    assert issue.priority == "high"
    assert issue.class_name == "dividend_policy"


def test_f13_without_fcf_is_insufficient() -> None:
    layout = simple_layout(
        [LayoutRow(row=2, label="Div")], sheet="CF", axis=YEARS
    )
    mapping = MappingDocument(
        rows=[mapped("CF", 2, "Div", "cf.dividends", role="calculation")]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("CF", "B2", "5"),
            cell("CF", "C2", "5"),
            cell("CF", "D2", "5"),
        ],
    )
    assert _control(doc, "F13").status == "insufficient"
    assert not any(i.control_id == "F13" for i in doc.issues)


def test_f13_fcfe_adds_draw_minus_repay() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="Div"),
            LayoutRow(row=3, label="FCF"),
            LayoutRow(row=4, label="Draw"),
            LayoutRow(row=5, label="Repay"),
        ],
        sheet="CF",
        axis=YEARS,
    )
    mapping = MappingDocument(
        rows=[
            mapped("CF", 2, "Div", "cf.dividends", role="calculation"),
            mapped("CF", 3, "FCF", "cf.fcf", role="output"),
            mapped("CF", 4, "Draw", "cf.drawdown", role="calculation"),
            mapped("CF", 5, "Repay", "cf.repayment", role="calculation"),
        ]
    )
    doc = run_frs(
        mapping,
        layout=layout,
        cells=[
            cell("CF", "B2", "0"),
            cell("CF", "C2", "5"),
            cell("CF", "D2", "5"),
            cell("CF", "B3", "4"),
            cell("CF", "C3", "-10"),
            cell("CF", "D3", "-8"),
            cell("CF", "B4", "0"),
            cell("CF", "C4", "20"),
            cell("CF", "D4", "20"),
            cell("CF", "B5", "0"),
            cell("CF", "C5", "0"),
            cell("CF", "D5", "0"),
        ],
    )
    row = _control(doc, "F13")
    assert row.status == "flagged"
    assert row.metrics.get("fcfe") == 10.0
    assert row.metrics.get("fcff") == -10.0
