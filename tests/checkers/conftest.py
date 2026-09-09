from __future__ import annotations

import json

from cashflow_audit.checkers.context import CheckContext
from cashflow_audit.compile.csr import build_csr
from cashflow_audit.compile.engine import FormulaEngine
from cashflow_audit.compile.models import Edge
from cashflow_audit.layout.models import Axis, AxisHeader, Block, Layout, LayoutRow, SheetLayout
from cashflow_audit.mapping.models import MappedRow, MappingDocument
from cashflow_audit.parse.a1 import format_addr, parse_addr
from cashflow_audit.series.models import SeriesOutlier


def cell(
    sheet: str,
    addr: str,
    value: str | None = None,
    *,
    formula: str | None = None,
    hidden: bool = False,
) -> dict:
    col, row = parse_addr(addr)
    engine = FormulaEngine(locale_hint="en")
    ast_json = None
    template = None
    if formula:
        parsed = engine.parse(formula, sheet=sheet, addr=addr)
        template = parsed.template
        if parsed.ast is not None:
            ast_json = json.dumps(parsed.ast, ensure_ascii=False)
    return {
        "sheet": sheet,
        "row": row,
        "col": col,
        "addr": addr,
        "cached_value": value,
        "formula_raw": formula,
        "formula_template": template,
        "ast_json": ast_json,
        "hidden": hidden,
        "unparsed": False,
        "number_format": None,
        "comment": None,
    }


def headers(*cols: tuple[int, str, str]) -> list[AxisHeader]:
    out = []
    for col, text, role in cols:
        out.append(
            AxisHeader(col=col, text=text, role=role, period_key=text)  # type: ignore[arg-type]
        )
    return out


def simple_layout(
    rows: list[LayoutRow],
    *,
    sheet: str = "P&L",
    axis: list[AxisHeader] | None = None,
) -> Layout:
    axis_headers = axis or headers((2, "2023", "historical"), (3, "2024E", "forecast"))
    return Layout(
        sheets=[
            SheetLayout(
                name=sheet,
                blocks=[
                    Block(
                        block_id=f"{sheet}!r1",
                        label_col=1,
                        axis=Axis(id=f"{sheet}!r1", row=1, headers=axis_headers),
                        rows=rows,
                    )
                ],
            )
        ]
    )


def mapped(
    sheet: str,
    row: int,
    label: str,
    concept_id: str | None,
    *,
    role: str = "calculation",
    block_id: str | None = None,
) -> MappedRow:
    bid = block_id or f"{sheet}!r1"
    return MappedRow(
        row_key=f"{sheet}|{row}|{bid}",
        sheet=sheet,
        row=row,
        block_id=bid,
        label=label,
        concept_id=concept_id,
        article_role=role,  # type: ignore[arg-type]
        source="glossary",
    )


def ctx(
    cells: list[dict],
    *,
    edges: list[Edge] | None = None,
    workbook: dict | None = None,
    layout: Layout | None = None,
    mapping: MappingDocument | None = None,
    series: list[SeriesOutlier] | None = None,
) -> CheckContext:
    cells = list(cells)
    extra = [f"{c['sheet']}!{c['addr']}" for c in cells]
    graph = build_csr(edges or [], extra_nodes=extra)
    return CheckContext(
        cells=cells,
        edges=edges or [],
        workbook=workbook
        or {"has_vba": False, "has_xlm": False, "externals": [], "iterate": False},
        layout=layout or simple_layout([]),
        mapping=mapping or MappingDocument(),
        series_outliers=series or [],
        csr=graph,
    )


def ref(sheet: str, addr: str) -> str:
    return f"{sheet}!{addr}"


def col_addr(col: int, row: int) -> str:
    return format_addr(col, row)
