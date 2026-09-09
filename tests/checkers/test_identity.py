from __future__ import annotations

from cashflow_audit.checkers.run import run_checks
from cashflow_audit.layout.models import Axis, Block, Layout, LayoutRow, SheetLayout
from cashflow_audit.mapping.models import MappingDocument
from tests.checkers.conftest import cell, ctx, headers, mapped, simple_layout


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


def test_i3a_cash_rollforward_break() -> None:
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
    assert [c for c in result.candidates if c.detector == "identity.I3a"]


def test_i3a_pairs_cash_and_cfo_across_sheets() -> None:
    bs = simple_layout([LayoutRow(row=2, label="Cash")], sheet="BS")
    cf = simple_layout([LayoutRow(row=3, label="CFO")], sheet="CF")
    layout = Layout(sheets=[*bs.sheets, *cf.sheets])
    mapping = MappingDocument(
        rows=[
            mapped("BS", 2, "Cash", "bs.cash", role="output"),
            mapped("CF", 3, "CFO", "cf.cfo", role="calculation"),
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
