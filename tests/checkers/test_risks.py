from __future__ import annotations

from cashflow_audit.checkers.run import run_checks
from cashflow_audit.layout.models import LayoutRow
from cashflow_audit.mapping.models import MappingDocument
from tests.checkers.conftest import cell, ctx, headers, mapped, simple_layout


def _rev_map() -> MappingDocument:
    return MappingDocument(
        rows=[mapped("P&L", 2, "Revenue", "pnl.revenue", role="output")]
    )


def test_revenue_drop_is_risk() -> None:
    layout = simple_layout([LayoutRow(row=2, label="Revenue")])
    result = run_checks(
        ctx(
            [cell("P&L", "B2", "100"), cell("P&L", "C2", "85")],
            layout=layout,
            mapping=_rev_map(),
        )
    )
    found = [c for c in result.candidates if c.detector == "risk.revenue_drop"]
    assert found
    assert found[0].base_severity == "risk"


def test_revenue_growth_is_not_risk() -> None:
    layout = simple_layout([LayoutRow(row=2, label="Revenue")])
    result = run_checks(
        ctx(
            [cell("P&L", "B2", "100"), cell("P&L", "C2", "110")],
            layout=layout,
            mapping=_rev_map(),
        )
    )
    assert not [c for c in result.candidates if c.detector == "risk.revenue_drop"]


def test_small_revenue_drop_is_not_risk() -> None:
    layout = simple_layout([LayoutRow(row=2, label="Revenue")])
    result = run_checks(
        ctx(
            [cell("P&L", "B2", "100"), cell("P&L", "C2", "95")],
            layout=layout,
            mapping=_rev_map(),
        )
    )
    assert not [c for c in result.candidates if c.detector == "risk.revenue_drop"]


def test_missing_revenue_is_silent_not_question() -> None:
    layout = simple_layout([LayoutRow(row=2, label="Sales")])
    result = run_checks(
        ctx(
            [cell("P&L", "B2", "100"), cell("P&L", "C2", "50")],
            layout=layout,
            mapping=MappingDocument(),
        )
    )
    assert not [c for c in result.candidates if c.detector.startswith("risk.")]
    assert not any(q.kind == "risk" for q in result.questions)


def test_negative_ebitda_streak_is_risk() -> None:
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
    result = run_checks(
        ctx(
            [
                cell("P&L", "B2", "10"),
                cell("P&L", "C2", "-5"),
                cell("P&L", "D2", "-8"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    found = [c for c in result.candidates if c.detector == "risk.negative_ebitda"]
    assert found
    assert found[0].base_severity == "risk"


def test_one_negative_ebitda_year_is_not_streak() -> None:
    layout = simple_layout([LayoutRow(row=2, label="EBITDA")])
    mapping = MappingDocument(
        rows=[mapped("P&L", 2, "EBITDA", "pnl.ebitda", role="output")]
    )
    result = run_checks(
        ctx(
            [cell("P&L", "B2", "10"), cell("P&L", "C2", "-5")],
            layout=layout,
            mapping=mapping,
        )
    )
    assert not [c for c in result.candidates if c.detector == "risk.negative_ebitda"]


def test_cash_negative_is_risk() -> None:
    layout = simple_layout([LayoutRow(row=2, label="Cash")], sheet="BS")
    mapping = MappingDocument(
        rows=[mapped("BS", 2, "Cash", "bs.cash", role="output")]
    )
    result = run_checks(
        ctx(
            [cell("BS", "B2", "10"), cell("BS", "C2", "-1")],
            layout=layout,
            mapping=mapping,
        )
    )
    found = [c for c in result.candidates if c.detector == "risk.cash_negative"]
    assert found
    assert found[0].base_severity == "risk"


def test_positive_cash_is_not_risk() -> None:
    layout = simple_layout([LayoutRow(row=2, label="Cash")], sheet="BS")
    mapping = MappingDocument(
        rows=[mapped("BS", 2, "Cash", "bs.cash", role="output")]
    )
    result = run_checks(
        ctx(
            [cell("BS", "B2", "10"), cell("BS", "C2", "5")],
            layout=layout,
            mapping=mapping,
        )
    )
    assert not [c for c in result.candidates if c.detector == "risk.cash_negative"]


def test_ebitda_margin_drop_is_risk() -> None:
    layout = simple_layout(
        [LayoutRow(row=2, label="Revenue"), LayoutRow(row=3, label="EBITDA")]
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "Revenue", "pnl.revenue", role="output"),
            mapped("P&L", 3, "EBITDA", "pnl.ebitda", role="output"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("P&L", "B2", "100"),
                cell("P&L", "C2", "100"),
                cell("P&L", "B3", "22"),
                cell("P&L", "C3", "14"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    found = [c for c in result.candidates if c.detector == "risk.ebitda_drop"]
    assert found


def test_icr_below_floor_is_risk() -> None:
    layout = simple_layout(
        [LayoutRow(row=2, label="EBIT"), LayoutRow(row=3, label="Interest")]
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "EBIT", "pnl.ebit", role="output"),
            mapped("P&L", 3, "Interest", "pnl.interest", role="calculation"),
        ]
    )
    result = run_checks(
        ctx(
            [cell("P&L", "B2", "10"), cell("P&L", "B3", "10")],
            layout=layout,
            mapping=mapping,
        )
    )
    found = [c for c in result.candidates if c.detector == "risk.icr"]
    assert found
    assert found[0].base_severity == "risk"


def test_debt_load_jump_is_risk() -> None:
    layout = simple_layout(
        [LayoutRow(row=2, label="Debt"), LayoutRow(row=3, label="EBITDA")],
        sheet="M",
    )
    mapping = MappingDocument(
        rows=[
            mapped("M", 2, "Debt", "bs.debt", role="output"),
            mapped("M", 3, "EBITDA", "pnl.ebitda", role="output"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("M", "B2", "100"),
                cell("M", "C2", "250"),
                cell("M", "B3", "50"),
                cell("M", "C3", "50"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    found = [c for c in result.candidates if c.detector == "risk.debt_load"]
    assert found


def test_dividends_exceed_cfo_is_risk() -> None:
    layout = simple_layout(
        [LayoutRow(row=2, label="CFO"), LayoutRow(row=3, label="Dividends")],
        sheet="CF",
    )
    mapping = MappingDocument(
        rows=[
            mapped("CF", 2, "CFO", "cf.cfo", role="output"),
            mapped("CF", 3, "Dividends", "cf.dividends", role="calculation"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("CF", "B2", "10"),
                cell("CF", "B3", "20"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    found = [c for c in result.candidates if c.detector == "risk.dividends_vs_cfo"]
    assert found
