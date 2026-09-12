from __future__ import annotations

from cashflow_audit.checkers.run import run_checks
from cashflow_audit.layout.detect import detect_layout
from cashflow_audit.layout.models import Axis, Block, Layout, LayoutRow, SheetLayout
from cashflow_audit.mapping.models import MappingDocument
from tests.checkers.conftest import cell, ctx, headers, mapped, simple_layout


def _stack_identity(*layouts: Layout) -> Layout:
    return Layout(sheets=[sheet for layout in layouts for sheet in layout.sheets])


def test_i1_imbalance_is_error() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="Assets"),
            LayoutRow(row=3, label="Equity"),
            LayoutRow(row=4, label="Liabilities"),
        ],
        sheet="BS",
    )
    mapping = MappingDocument(
        rows=[
            mapped("BS", 2, "Assets", "bs.assets_total", role="output"),
            mapped("BS", 3, "Equity", "bs.equity", role="output"),
            mapped("BS", 4, "Liabilities", "bs.liabilities", role="output"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("BS", "B2", "100"),
                cell("BS", "B3", "40"),
                cell("BS", "B4", "50"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    found = [c for c in result.candidates if c.detector == "identity.I1"]
    assert found
    assert found[0].base_severity == "error"


def test_i1_balanced_is_negative() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="Assets"),
            LayoutRow(row=3, label="Equity"),
            LayoutRow(row=4, label="Liabilities"),
        ],
        sheet="BS",
    )
    mapping = MappingDocument(
        rows=[
            mapped("BS", 2, "Assets", "bs.assets_total", role="output"),
            mapped("BS", 3, "Equity", "bs.equity", role="output"),
            mapped("BS", 4, "Liabilities", "bs.liabilities", role="output"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("BS", "B2", "100"),
                cell("BS", "B3", "40"),
                cell("BS", "B4", "60"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    assert not [c for c in result.candidates if c.detector == "identity.I1"]


def test_i1_without_concept_is_question_not_finding() -> None:
    layout = simple_layout(
        [LayoutRow(row=2, label="Assets"), LayoutRow(row=3, label="Equity")],
        sheet="BS",
    )
    mapping = MappingDocument(
        rows=[mapped("BS", 2, "Assets", "bs.assets_total", role="output")]
    )
    result = run_checks(ctx([cell("BS", "B2", "100")], layout=layout, mapping=mapping))
    assert not [c for c in result.candidates if c.detector.startswith("identity.")]
    assert result.questions
    assert result.questions[0].kind == "identity_gap"


def test_check_row_is_not_second_i1_finding() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="Assets"),
            LayoutRow(row=3, label="Equity"),
            LayoutRow(row=4, label="Liabilities"),
            LayoutRow(row=5, label="Check", check_row=True),
        ],
        sheet="BS",
    )
    mapping = MappingDocument(
        rows=[
            mapped("BS", 2, "Assets", "bs.assets_total", role="output"),
            mapped("BS", 3, "Equity", "bs.equity", role="output"),
            mapped("BS", 4, "Liabilities", "bs.liabilities", role="output"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("BS", "B2", "100"),
                cell("BS", "B3", "40"),
                cell("BS", "B4", "50"),
                cell("BS", "B5", "10"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    found = [c for c in result.candidates if c.detector == "identity.I1"]
    assert len(found) == 1


def test_i3a_does_not_treat_cfo_as_net_cash_flow() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="Cash"),
            LayoutRow(row=3, label="CFO"),
        ],
        sheet="CF",
    )
    mapping = MappingDocument(
        rows=[
            mapped("CF", 2, "Cash", "bs.cash", role="output"),
            mapped("CF", 3, "CFO", "cf.cfo", role="calculation"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("CF", "B2", "100"),
                cell("CF", "C2", "130"),
                cell("CF", "B3", "10"),
                cell("CF", "C3", "10"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    assert not [c for c in result.candidates if c.detector == "identity.I3a"]


def test_i3a_fcf_rollforward_break() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="Cash"),
            LayoutRow(row=3, label="FCF"),
        ],
        sheet="CF",
    )
    mapping = MappingDocument(
        rows=[
            mapped("CF", 2, "Cash", "bs.cash", role="output"),
            mapped("CF", 3, "FCF", "cf.fcf", role="calculation"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("CF", "B2", "100"),
                cell("CF", "C2", "130"),
                cell("CF", "B3", "10"),
                cell("CF", "C3", "10"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    found = [c for c in result.candidates if c.detector == "identity.I3a"]
    assert found
    assert "CF!B2" in found[0].cell_refs
    assert "CF!C2" in found[0].cell_refs
    assert "CF!C3" in found[0].cell_refs


def test_i3a_pairs_cash_and_fcf_across_sheets() -> None:
    bs = simple_layout([LayoutRow(row=2, label="Cash")], sheet="BS")
    cf = simple_layout([LayoutRow(row=3, label="FCF")], sheet="CF")
    layout = Layout(sheets=[*bs.sheets, *cf.sheets])
    mapping = MappingDocument(
        rows=[
            mapped("BS", 2, "Cash", "bs.cash", role="output"),
            mapped("CF", 3, "FCF", "cf.fcf", role="calculation"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("BS", "B2", "100"),
                cell("BS", "C2", "130"),
                cell("CF", "B3", "10"),
                cell("CF", "C3", "10"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    assert [c for c in result.candidates if c.detector == "identity.I3a"]


def test_i3a_aligns_periods_by_key_not_column() -> None:
    bs_axis = headers((2, "2023", "historical"), (3, "2024E", "forecast"))
    cf_axis = headers((5, "2023", "historical"), (6, "2024E", "forecast"))
    bs = Layout(
        sheets=[
            SheetLayout(
                name="BS",
                blocks=[
                    Block(
                        block_id="BS!r1",
                        label_col=1,
                        axis=Axis(id="BS!r1", row=1, headers=bs_axis),
                        rows=[LayoutRow(row=2, label="Cash")],
                    )
                ],
            )
        ]
    )
    cf = Layout(
        sheets=[
            SheetLayout(
                name="CF",
                blocks=[
                    Block(
                        block_id="CF!r1",
                        label_col=1,
                        axis=Axis(id="CF!r1", row=1, headers=cf_axis),
                        rows=[LayoutRow(row=3, label="FCF")],
                    )
                ],
            )
        ]
    )
    layout = Layout(sheets=[*bs.sheets, *cf.sheets])
    mapping = MappingDocument(
        rows=[
            mapped("BS", 2, "Cash", "bs.cash", role="output"),
            mapped("CF", 3, "FCF", "cf.fcf", role="calculation"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("BS", "B2", "100"),
                cell("BS", "C2", "130"),
                cell("CF", "E3", "10"),
                cell("CF", "F3", "10"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    found = [c for c in result.candidates if c.detector == "identity.I3a"]
    assert found
    assert found[0].cell_refs == ["BS!B2", "BS!C2", "CF!F3"]


def test_i3a_joins_plain_year_to_e_suffix() -> None:
    layout = detect_layout(
        [
            cell("BS", "A1", "Item"),
            cell("BS", "B1", "2024"),
            cell("BS", "C1", "2025"),
            cell("BS", "A2", "Cash"),
            cell("BS", "B2", "100"),
            cell("BS", "C2", "130"),
            cell("CF", "A1", "Item"),
            cell("CF", "B1", "2024E"),
            cell("CF", "C1", "2025E"),
            cell("CF", "A2", "FCF"),
            cell("CF", "B2", "10"),
            cell("CF", "C2", "10"),
        ]
    )
    mapping = MappingDocument(
        rows=[
            mapped("BS", 2, "Cash", "bs.cash", role="output"),
            mapped("CF", 2, "FCF", "cf.fcf", role="calculation"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("BS", "B2", "100"),
                cell("BS", "C2", "130"),
                cell("CF", "B2", "10"),
                cell("CF", "C2", "10"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    assert [c for c in result.candidates if c.detector == "identity.I3a"]


def test_i3b_without_re_concept_is_question() -> None:
    layout = simple_layout([LayoutRow(row=2, label="NI")], sheet="P&L")
    mapping = MappingDocument(
        rows=[mapped("P&L", 2, "NI", "pnl.net_income", role="output")]
    )
    result = run_checks(ctx([cell("P&L", "B2", "10")], layout=layout, mapping=mapping))
    kinds = {q.kind for q in result.questions}
    assert "identity_gap" in kinds
    assert not [c for c in result.candidates if c.detector == "identity.I3b"]


def test_i1_reports_every_broken_period() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="Assets"),
            LayoutRow(row=3, label="Equity"),
            LayoutRow(row=4, label="Liabilities"),
        ],
        sheet="BS",
        axis=headers(
            (2, "2023", "historical"),
            (3, "2024E", "forecast"),
            (4, "2025E", "forecast"),
        ),
    )
    mapping = MappingDocument(
        rows=[
            mapped("BS", 2, "Assets", "bs.assets_total", role="output"),
            mapped("BS", 3, "Equity", "bs.equity", role="output"),
            mapped("BS", 4, "Liabilities", "bs.liabilities", role="output"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("BS", "B2", "100"),
                cell("BS", "B3", "40"),
                cell("BS", "B4", "60"),
                cell("BS", "C2", "100"),
                cell("BS", "C3", "40"),
                cell("BS", "C4", "50"),
                cell("BS", "D2", "200"),
                cell("BS", "D3", "80"),
                cell("BS", "D4", "90"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    found = [c for c in result.candidates if c.detector == "identity.I1"]
    cols = {c.payload["col"] for c in found}
    assert cols == {3, 4}


def test_i1_does_not_mix_totals_from_two_blocks() -> None:
    axis = headers((2, "2023", "historical"))
    layout = Layout(
        sheets=[
            SheetLayout(
                name="BS",
                blocks=[
                    Block(
                        block_id="BS!r1",
                        label_col=1,
                        axis=Axis(id="BS!r1", row=1, headers=axis),
                        rows=[
                            LayoutRow(row=2, label="Assets"),
                            LayoutRow(row=3, label="Equity"),
                            LayoutRow(row=4, label="Liabilities"),
                        ],
                    ),
                    Block(
                        block_id="BS!r10",
                        label_col=1,
                        axis=Axis(id="BS!r10", row=10, headers=axis),
                        rows=[
                            LayoutRow(row=12, label="Assets"),
                            LayoutRow(row=13, label="Equity"),
                            LayoutRow(row=14, label="Liabilities"),
                        ],
                    ),
                ],
            )
        ]
    )
    mapping = MappingDocument(
        rows=[
            mapped("BS", 2, "Assets", "bs.assets_total", role="output"),
            mapped("BS", 3, "Equity", "bs.equity", role="output"),
            mapped("BS", 4, "Liabilities", "bs.liabilities", role="output"),
            mapped("BS", 12, "Assets", "bs.assets_total", role="output", block_id="BS!r10"),
            mapped("BS", 13, "Equity", "bs.equity", role="output", block_id="BS!r10"),
            mapped("BS", 14, "Liabilities", "bs.liabilities", role="output", block_id="BS!r10"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("BS", "B2", "100"),
                cell("BS", "B3", "40"),
                cell("BS", "B4", "60"),
                cell("BS", "B12", "100"),
                cell("BS", "B13", "40"),
                cell("BS", "B14", "50"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    found = [c for c in result.candidates if c.detector == "identity.I1"]
    assert len(found) == 1
    assert found[0].cell_refs == ["BS!B12", "BS!B13", "BS!B14"]


def test_i1_ignores_scenario_columns() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="Assets"),
            LayoutRow(row=3, label="Equity"),
            LayoutRow(row=4, label="Liabilities"),
        ],
        sheet="BS",
        axis=headers((2, "2023", "historical"), (3, "Base", "scenario")),
    )
    mapping = MappingDocument(
        rows=[
            mapped("BS", 2, "Assets", "bs.assets_total", role="output"),
            mapped("BS", 3, "Equity", "bs.equity", role="output"),
            mapped("BS", 4, "Liabilities", "bs.liabilities", role="output"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("BS", "B2", "100"),
                cell("BS", "B3", "40"),
                cell("BS", "B4", "60"),
                cell("BS", "C2", "100"),
                cell("BS", "C3", "40"),
                cell("BS", "C4", "50"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    assert not [c for c in result.candidates if c.detector == "identity.I1"]


def test_i5_gross_profit_mismatch_is_error() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="Revenue"),
            LayoutRow(row=3, label="COGS"),
            LayoutRow(row=4, label="Gross profit"),
        ]
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "Revenue", "pnl.revenue", role="output"),
            mapped("P&L", 3, "COGS", "pnl.cogs", role="calculation"),
            mapped("P&L", 4, "Gross profit", "pnl.gross_profit", role="output"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("P&L", "B2", "100"),
                cell("P&L", "B3", "40"),
                cell("P&L", "B4", "50"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    found = [c for c in result.candidates if c.detector == "identity.I5"]
    assert found
    assert found[0].base_severity == "error"
    assert found[0].payload["delta"] == -10.0
    assert found[0].cell_refs == ["P&L!B4", "P&L!B2", "P&L!B3"]


def test_i5_balanced_is_negative() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="Revenue"),
            LayoutRow(row=3, label="COGS"),
            LayoutRow(row=4, label="Gross profit"),
        ]
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "Revenue", "pnl.revenue", role="output"),
            mapped("P&L", 3, "COGS", "pnl.cogs", role="calculation"),
            mapped("P&L", 4, "Gross profit", "pnl.gross_profit", role="output"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("P&L", "B2", "100"),
                cell("P&L", "B3", "40"),
                cell("P&L", "B4", "60"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    assert not [c for c in result.candidates if c.detector == "identity.I5"]


def test_i5_without_cogs_is_question_not_finding() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="Revenue"),
            LayoutRow(row=4, label="Gross profit"),
        ]
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "Revenue", "pnl.revenue", role="output"),
            mapped("P&L", 4, "Gross profit", "pnl.gross_profit", role="output"),
        ]
    )
    result = run_checks(
        ctx(
            [cell("P&L", "B2", "100"), cell("P&L", "B4", "60")],
            layout=layout,
            mapping=mapping,
        )
    )
    assert not [c for c in result.candidates if c.detector == "identity.I5"]
    assert any("I5" in (q.prompt or "") for q in result.questions)


def test_i5_reports_every_broken_period() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="Revenue"),
            LayoutRow(row=3, label="COGS"),
            LayoutRow(row=4, label="Gross profit"),
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
            mapped("P&L", 3, "COGS", "pnl.cogs", role="calculation"),
            mapped("P&L", 4, "Gross profit", "pnl.gross_profit", role="output"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("P&L", "B2", "100"),
                cell("P&L", "B3", "40"),
                cell("P&L", "B4", "60"),
                cell("P&L", "C2", "100"),
                cell("P&L", "C3", "40"),
                cell("P&L", "C4", "50"),
                cell("P&L", "D2", "200"),
                cell("P&L", "D3", "80"),
                cell("P&L", "D4", "90"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    cols = {c.payload["col"] for c in result.candidates if c.detector == "identity.I5"}
    assert cols == {3, 4}


def test_i7_ebit_mismatch_is_error() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="EBITDA"),
            LayoutRow(row=3, label="D&A"),
            LayoutRow(row=4, label="EBIT"),
        ]
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "EBITDA", "pnl.ebitda", role="output"),
            mapped("P&L", 3, "D&A", "pnl.da", role="calculation"),
            mapped("P&L", 4, "EBIT", "pnl.ebit", role="output"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("P&L", "B2", "50"),
                cell("P&L", "B3", "10"),
                cell("P&L", "B4", "30"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    found = [c for c in result.candidates if c.detector == "identity.I7"]
    assert found
    assert found[0].base_severity == "error"
    assert found[0].payload["delta"] == -10.0


def test_i10_debt_rollforward_break_is_error() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="Debt"),
            LayoutRow(row=3, label="Draw"),
            LayoutRow(row=4, label="Repay"),
        ],
        sheet="BS",
        axis=headers((2, "2023", "historical"), (3, "2024E", "forecast")),
    )
    mapping = MappingDocument(
        rows=[
            mapped("BS", 2, "Debt", "bs.debt", role="output"),
            mapped("BS", 3, "Draw", "cf.drawdown", role="calculation"),
            mapped("BS", 4, "Repay", "cf.repayment", role="calculation"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("BS", "B2", "100"),
                cell("BS", "C2", "150"),
                cell("BS", "B3", "0"),
                cell("BS", "C3", "50"),
                cell("BS", "B4", "0"),
                cell("BS", "C4", "20"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    found = [c for c in result.candidates if c.detector == "identity.I10"]
    assert found
    assert found[0].base_severity == "error"
    assert "BS!B2" in found[0].cell_refs
    assert "BS!C2" in found[0].cell_refs
    assert "BS!C3" in found[0].cell_refs
    assert "BS!C4" in found[0].cell_refs


def test_i10_balanced_rollforward_is_silent() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="Debt"),
            LayoutRow(row=3, label="Draw"),
            LayoutRow(row=4, label="Repay"),
        ],
        sheet="BS",
        axis=headers((2, "2023", "historical"), (3, "2024E", "forecast")),
    )
    mapping = MappingDocument(
        rows=[
            mapped("BS", 2, "Debt", "bs.debt", role="output"),
            mapped("BS", 3, "Draw", "cf.drawdown", role="calculation"),
            mapped("BS", 4, "Repay", "cf.repayment", role="calculation"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("BS", "B2", "100"),
                cell("BS", "C2", "130"),
                cell("BS", "C3", "50"),
                cell("BS", "C4", "20"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    assert not [c for c in result.candidates if c.detector == "identity.I10"]


def test_i10_without_flows_is_question_not_finding() -> None:
    layout = simple_layout(
        [LayoutRow(row=2, label="Debt")],
        sheet="BS",
        axis=headers((2, "2023", "historical"), (3, "2024E", "forecast")),
    )
    mapping = MappingDocument(
        rows=[mapped("BS", 2, "Debt", "bs.debt", role="output")]
    )
    result = run_checks(
        ctx(
            [cell("BS", "B2", "100"), cell("BS", "C2", "150")],
            layout=layout,
            mapping=mapping,
        )
    )
    assert not [c for c in result.candidates if c.detector == "identity.I10"]
    assert any("I10" in (q.prompt or "") for q in result.questions)


def test_i7_balanced_is_negative() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="EBITDA"),
            LayoutRow(row=3, label="D&A"),
            LayoutRow(row=4, label="EBIT"),
        ]
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "EBITDA", "pnl.ebitda", role="output"),
            mapped("P&L", 3, "D&A", "pnl.da", role="calculation"),
            mapped("P&L", 4, "EBIT", "pnl.ebit", role="output"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("P&L", "B2", "50"),
                cell("P&L", "B3", "10"),
                cell("P&L", "B4", "40"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    assert not [c for c in result.candidates if c.detector == "identity.I7"]


def test_i7_without_da_is_question_not_finding() -> None:
    layout = simple_layout(
        [LayoutRow(row=2, label="EBITDA"), LayoutRow(row=4, label="EBIT")]
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "EBITDA", "pnl.ebitda", role="output"),
            mapped("P&L", 4, "EBIT", "pnl.ebit", role="output"),
        ]
    )
    result = run_checks(
        ctx(
            [cell("P&L", "B2", "50"), cell("P&L", "B4", "40")],
            layout=layout,
            mapping=mapping,
        )
    )
    assert not [c for c in result.candidates if c.detector == "identity.I7"]
    assert any("I7" in (q.prompt or "") for q in result.questions)


def test_i9_cfo_bridge_break_is_error() -> None:
    layout = _stack_identity(
        simple_layout(
            [LayoutRow(row=2, label="NI"), LayoutRow(row=3, label="DA")],
            sheet="P&L",
            axis=headers((2, "2023", "historical"), (3, "2024E", "forecast")),
        ),
        simple_layout(
            [LayoutRow(row=2, label="AR")],
            sheet="BS",
            axis=headers((2, "2023", "historical"), (3, "2024E", "forecast")),
        ),
        simple_layout(
            [LayoutRow(row=2, label="CFO")],
            sheet="CF",
            axis=headers((2, "2023", "historical"), (3, "2024E", "forecast")),
        ),
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "NI", "pnl.net_income", role="output"),
            mapped("P&L", 3, "DA", "pnl.da", role="calculation"),
            mapped("BS", 2, "AR", "bs.ar", role="output"),
            mapped("CF", 2, "CFO", "cf.cfo", role="output"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("P&L", "B2", "10"),
                cell("P&L", "C2", "10"),
                cell("P&L", "B3", "2"),
                cell("P&L", "C3", "2"),
                cell("BS", "B2", "50"),
                cell("BS", "C2", "60"),
                cell("CF", "B2", "12"),
                cell("CF", "C2", "20"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    found = [c for c in result.candidates if c.detector == "identity.I9"]
    assert found
    assert found[0].base_severity == "error"
    assert "CF!C2" in found[0].cell_refs
    assert "P&L!C2" in found[0].cell_refs


def test_i9_cfo_bridge_balanced_is_silent() -> None:
    layout = _stack_identity(
        simple_layout(
            [LayoutRow(row=2, label="NI"), LayoutRow(row=3, label="DA")],
            sheet="P&L",
            axis=headers((2, "2023", "historical"), (3, "2024E", "forecast")),
        ),
        simple_layout(
            [LayoutRow(row=2, label="AR")],
            sheet="BS",
            axis=headers((2, "2023", "historical"), (3, "2024E", "forecast")),
        ),
        simple_layout(
            [LayoutRow(row=2, label="CFO")],
            sheet="CF",
            axis=headers((2, "2023", "historical"), (3, "2024E", "forecast")),
        ),
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "NI", "pnl.net_income", role="output"),
            mapped("P&L", 3, "DA", "pnl.da", role="calculation"),
            mapped("BS", 2, "AR", "bs.ar", role="output"),
            mapped("CF", 2, "CFO", "cf.cfo", role="output"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("P&L", "C2", "10"),
                cell("P&L", "C3", "2"),
                cell("BS", "B2", "50"),
                cell("BS", "C2", "60"),
                cell("CF", "C2", "2"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    assert not [c for c in result.candidates if c.detector == "identity.I9"]


def test_i9_without_da_or_wc_is_question_not_finding() -> None:
    layout = _stack_identity(
        simple_layout(
            [LayoutRow(row=2, label="NI")],
            sheet="P&L",
            axis=headers((2, "2023", "historical"), (3, "2024E", "forecast")),
        ),
        simple_layout(
            [LayoutRow(row=2, label="CFO")],
            sheet="CF",
            axis=headers((2, "2023", "historical"), (3, "2024E", "forecast")),
        ),
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "NI", "pnl.net_income", role="output"),
            mapped("CF", 2, "CFO", "cf.cfo", role="output"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("P&L", "B2", "10"),
                cell("P&L", "C2", "12"),
                cell("CF", "B2", "8"),
                cell("CF", "C2", "9"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    assert not [c for c in result.candidates if c.detector == "identity.I9"]
    assert any("I9" in (q.prompt or "") for q in result.questions)


def test_i11_interest_mismatch_is_error() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="Debt"),
            LayoutRow(row=3, label="Interest"),
            LayoutRow(row=4, label="Rate"),
        ],
        sheet="M",
        axis=headers((2, "2023", "historical"), (3, "2024E", "forecast")),
    )
    mapping = MappingDocument(
        rows=[
            mapped("M", 2, "Debt", "bs.debt", role="output"),
            mapped("M", 3, "Interest", "pnl.interest", role="calculation"),
            mapped("M", 4, "Rate", "pnl.interest_rate", role="assumption"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("M", "B2", "100"),
                cell("M", "C2", "100"),
                cell("M", "B3", "12"),
                cell("M", "C3", "7"),
                cell("M", "B4", "0.12"),
                cell("M", "C4", "0.12"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    found = [c for c in result.candidates if c.detector == "identity.I11"]
    assert found
    assert found[0].base_severity == "error"
    assert "M!C3" in found[0].cell_refs
    assert "M!C4" in found[0].cell_refs


def test_i11_percent_rate_and_average_debt_is_silent() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="Debt"),
            LayoutRow(row=3, label="Interest"),
            LayoutRow(row=4, label="Rate"),
        ],
        sheet="M",
        axis=headers((2, "2023", "historical"), (3, "2024E", "forecast")),
    )
    mapping = MappingDocument(
        rows=[
            mapped("M", 2, "Debt", "bs.debt", role="output"),
            mapped("M", 3, "Interest", "pnl.interest", role="calculation"),
            mapped("M", 4, "Rate", "pnl.interest_rate", role="assumption"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("M", "B2", "100"),
                cell("M", "C2", "100"),
                cell("M", "C3", "12"),
                cell("M", "C4", "12"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    assert not [c for c in result.candidates if c.detector == "identity.I11"]


def test_i11_without_rate_is_question_not_finding() -> None:
    layout = simple_layout(
        [LayoutRow(row=2, label="Debt"), LayoutRow(row=3, label="Interest")],
        sheet="M",
        axis=headers((2, "2023", "historical"), (3, "2024E", "forecast")),
    )
    mapping = MappingDocument(
        rows=[
            mapped("M", 2, "Debt", "bs.debt", role="output"),
            mapped("M", 3, "Interest", "pnl.interest", role="calculation"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("M", "B2", "100"),
                cell("M", "C2", "100"),
                cell("M", "C3", "12"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    assert not [c for c in result.candidates if c.detector == "identity.I11"]
    assert any("I11" in (q.prompt or "") for q in result.questions)


def test_i12_tax_mismatch_is_error() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="Tax"),
            LayoutRow(row=3, label="NI"),
            LayoutRow(row=4, label="Rate"),
        ],
        sheet="P&L",
        axis=headers((2, "2023", "historical")),
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "Tax", "pnl.tax", role="calculation"),
            mapped("P&L", 3, "NI", "pnl.net_income", role="output"),
            mapped("P&L", 4, "Rate", "pnl.tax_rate", role="assumption"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("P&L", "B2", "14"),
                cell("P&L", "B3", "86"),
                cell("P&L", "B4", "0.25"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    found = [c for c in result.candidates if c.detector == "identity.I12"]
    assert found
    assert found[0].base_severity == "error"
    assert "P&L!B2" in found[0].cell_refs
    assert "P&L!B4" in found[0].cell_refs


def test_i12_percent_rate_and_balanced_is_silent() -> None:
    layout = simple_layout(
        [
            LayoutRow(row=2, label="Tax"),
            LayoutRow(row=3, label="NI"),
            LayoutRow(row=4, label="Rate"),
        ],
        sheet="P&L",
        axis=headers((2, "2023", "historical")),
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "Tax", "pnl.tax", role="calculation"),
            mapped("P&L", 3, "NI", "pnl.net_income", role="output"),
            mapped("P&L", 4, "Rate", "pnl.tax_rate", role="assumption"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("P&L", "B2", "25"),
                cell("P&L", "B3", "75"),
                cell("P&L", "B4", "25"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    assert not [c for c in result.candidates if c.detector == "identity.I12"]


def test_i12_without_rate_is_question_not_finding() -> None:
    layout = simple_layout(
        [LayoutRow(row=2, label="Tax"), LayoutRow(row=3, label="NI")],
        sheet="P&L",
        axis=headers((2, "2023", "historical")),
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "Tax", "pnl.tax", role="calculation"),
            mapped("P&L", 3, "NI", "pnl.net_income", role="output"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("P&L", "B2", "25"),
                cell("P&L", "B3", "75"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    assert not [c for c in result.candidates if c.detector == "identity.I12"]
    assert any("I12" in (q.prompt or "") for q in result.questions)


def test_i8_da_addback_mismatch_is_error() -> None:
    layout = _stack_identity(
        simple_layout(
            [LayoutRow(row=2, label="D&A")],
            sheet="P&L",
            axis=headers((2, "2023", "historical")),
        ),
        simple_layout(
            [LayoutRow(row=2, label="D&A add-back")],
            sheet="CF",
            axis=headers((2, "2023", "historical")),
        ),
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "D&A", "pnl.da", role="calculation"),
            mapped("CF", 2, "D&A add-back", "cf.da", role="calculation"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("P&L", "B2", "10"),
                cell("CF", "B2", "5"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    found = [c for c in result.candidates if c.detector == "identity.I8"]
    assert found
    assert found[0].base_severity == "error"
    assert "P&L!B2" in found[0].cell_refs
    assert "CF!B2" in found[0].cell_refs


def test_i8_opposite_sign_same_magnitude_is_silent() -> None:
    layout = _stack_identity(
        simple_layout(
            [LayoutRow(row=2, label="D&A")],
            sheet="P&L",
            axis=headers((2, "2023", "historical")),
        ),
        simple_layout(
            [LayoutRow(row=2, label="D&A add-back")],
            sheet="CF",
            axis=headers((2, "2023", "historical")),
        ),
    )
    mapping = MappingDocument(
        rows=[
            mapped("P&L", 2, "D&A", "pnl.da", role="calculation"),
            mapped("CF", 2, "D&A add-back", "cf.da", role="calculation"),
        ]
    )
    result = run_checks(
        ctx(
            [
                cell("P&L", "B2", "10"),
                cell("CF", "B2", "-10"),
            ],
            layout=layout,
            mapping=mapping,
        )
    )
    assert not [c for c in result.candidates if c.detector == "identity.I8"]


def test_i8_without_cf_addback_is_question_not_finding() -> None:
    layout = simple_layout(
        [LayoutRow(row=2, label="D&A")],
        sheet="P&L",
        axis=headers((2, "2023", "historical")),
    )
    mapping = MappingDocument(
        rows=[mapped("P&L", 2, "D&A", "pnl.da", role="calculation")]
    )
    result = run_checks(
        ctx(
            [cell("P&L", "B2", "10")],
            layout=layout,
            mapping=mapping,
        )
    )
    assert not [c for c in result.candidates if c.detector == "identity.I8"]
    assert any("I8" in (q.prompt or "") for q in result.questions)
