from __future__ import annotations

import json

from cashflow_audit.compile.engine import FormulaEngine
from cashflow_audit.layout.models import Axis, AxisHeader, Block, Layout, LayoutRow, SheetLayout
from cashflow_audit.parse.a1 import format_addr
from cashflow_audit.series.analyze import KINDS, analyze_series


def _headers() -> list[AxisHeader]:
    return [
        AxisHeader(col=2, text="2023", role="historical", period_key="2023"),
        AxisHeader(col=3, text="2024E", role="forecast", period_key="2024E"),
        AxisHeader(col=4, text="2025E", role="forecast", period_key="2025E"),
        AxisHeader(col=5, text="2026E", role="forecast", period_key="2026E"),
    ]


def _layout(row: int = 2) -> Layout:
    return Layout(
        sheets=[
            SheetLayout(
                name="P&L",
                blocks=[
                    Block(
                        block_id="P&L!r1",
                        label_col=1,
                        axis=Axis(id="P&L!r1", row=1, headers=_headers()),
                        rows=[LayoutRow(row=row, label="Revenue")],
                    )
                ],
            )
        ]
    )


def _cell(
    col: int,
    *,
    row: int = 2,
    formula: str | None = None,
    value: str | None = "1",
) -> dict:
    engine = FormulaEngine(locale_hint="en")
    addr = format_addr(col, row)
    template = None
    ast_json = None
    if formula:
        parsed = engine.parse(formula, sheet="P&L", addr=addr)
        template = parsed.template
        ast_json = json.dumps(parsed.ast, ensure_ascii=False) if parsed.ast else None
    return {
        "sheet": "P&L",
        "row": row,
        "col": col,
        "addr": addr,
        "formula_raw": formula,
        "formula_template": template,
        "ast_json": ast_json,
        "cached_value": value,
        "hidden": False,
        "unparsed": False,
        "number_format": None,
        "comment": None,
    }


def _copied_formulas(last: dict | None = None) -> list[dict]:
    cells = [
        _cell(2, formula="=A2", value="10"),
        _cell(3, formula="=B2", value="11"),
        _cell(4, formula="=C2", value="12"),
    ]
    cells.append(last if last is not None else _cell(5, formula="=D2", value="13"))
    return cells


def test_kinds_are_the_plan_names() -> None:
    assert KINDS == {
        "template_change",
        "value_instead_of_formula",
        "literal_in_ast",
        "ref_shift",
        "source_sheet_change",
    }


def test_majority_below_70_percent_is_silent() -> None:
    cells = [
        _cell(2, formula="=A2"),
        _cell(3, formula="=A2+B2"),
        _cell(4, formula="=A2*2"),
        _cell(5, formula="=SUM(A2:B2)"),
    ]
    outliers = analyze_series(_layout(), cells)
    assert outliers == []


def test_mode_empty_formulas_is_not_analyzed() -> None:
    cells = [
        _cell(2, formula=None, value="10"),
        _cell(3, formula=None, value="11"),
        _cell(4, formula=None, value="12"),
        _cell(5, formula=None, value="13"),
    ]
    assert analyze_series(_layout(), cells) == []


def test_value_instead_of_formula() -> None:
    cells = _copied_formulas(_cell(5, formula=None, value="1500"))
    outliers = analyze_series(_layout(), cells)
    assert [o.kind for o in outliers] == ["value_instead_of_formula"]
    assert outliers[0].cell_ref == "P&L!E2"


def test_template_change() -> None:
    cells = _copied_formulas(_cell(5, formula="=SUM(D2)", value="13"))
    outliers = analyze_series(_layout(), cells)
    assert outliers[0].kind == "template_change"


def test_literal_in_ast() -> None:
    cells = _copied_formulas(_cell(5, formula="=D2*1.2", value="15"))
    outliers = analyze_series(_layout(), cells)
    assert outliers[0].kind == "literal_in_ast"


def test_ref_shift() -> None:
    cells = _copied_formulas(_cell(5, formula="=C2", value="12"))
    outliers = analyze_series(_layout(), cells)
    assert outliers[0].kind == "ref_shift"


def test_source_sheet_change() -> None:
    cells = _copied_formulas(_cell(5, formula="=Inputs!D5", value="1"))
    outliers = analyze_series(_layout(), cells)
    assert outliers[0].kind == "source_sheet_change"


def test_forecast_edge_period_flag() -> None:
    cells = _copied_formulas(_cell(5, formula=None, value="1500"))
    outliers = analyze_series(_layout(), cells)
    assert outliers[0].kind == "value_instead_of_formula"
    assert outliers[0].edge_period is True


def test_middle_forecast_is_not_edge_period() -> None:
    cells = [
        _cell(2, formula="=A2"),
        _cell(3, formula="=B2"),
        _cell(4, formula=None, value="99"),
        _cell(5, formula="=D2"),
    ]
    outliers = analyze_series(_layout(), cells)
    assert len(outliers) == 1
    assert outliers[0].col == 4
    assert outliers[0].edge_period is False


def test_check_row_is_not_analyzed() -> None:
    layout = Layout(
        sheets=[
            SheetLayout(
                name="P&L",
                blocks=[
                    Block(
                        block_id="P&L!r1",
                        label_col=1,
                        axis=Axis(id="P&L!r1", row=1, headers=_headers()),
                        rows=[LayoutRow(row=2, label="Check", check_row=True)],
                    )
                ],
            )
        ]
    )
    cells = _copied_formulas(_cell(5, formula=None, value="1"))
    assert analyze_series(layout, cells) == []
