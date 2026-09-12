from __future__ import annotations

from pathlib import Path

from tests.checkers.conftest import cell, headers, mapped, simple_layout

from cashflow_audit.frs.router import run_frs
from cashflow_audit.frs.stage import frs_workbook
from cashflow_audit.layout.models import Layout, LayoutRow
from cashflow_audit.mapping.models import MappingDocument
from cashflow_audit.store.fs import write_json, write_parquet

YEARS = headers(
    (2, "2023", "historical"),
    (3, "2024E", "forecast"),
    (4, "2025E", "forecast"),
)


def _control(doc, fid: str):
    return next(c for c in doc.controls if c.id == fid)


def _stack(*layouts: Layout) -> Layout:
    return Layout(sheets=[sheet for layout in layouts for sheet in layout.sheets])


def test_i1_caps_f06_confidence_to_low() -> None:
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
    cells = [
        cell("BS", "B2", "100"),
        cell("BS", "C2", "216"),
        cell("BS", "D2", "216"),
        cell("P&L", "B2", "58"),
        cell("P&L", "C2", "151"),
        cell("P&L", "D2", "160"),
    ]
    silent = run_frs(mapping, layout=layout, cells=cells)
    broken = run_frs(
        mapping,
        layout=layout,
        cells=cells,
        identity=frozenset({"identity.I1"}),
    )
    assert _control(silent, "F06").status == "clear"
    assert _control(silent, "F06").confidence == "medium"
    assert _control(broken, "F06").confidence == "low"
    assert _control(broken, "F08").status == "not_applicable"


def test_i3a_caps_mapped_f04_to_low() -> None:
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
    cells = [
        cell("P&L", "B2", "10"),
        cell("P&L", "C2", "12"),
        cell("P&L", "D2", "14"),
        cell("CF", "B2", "8"),
        cell("CF", "C2", "9"),
        cell("CF", "D2", "11"),
    ]
    silent = run_frs(mapping, layout=layout, cells=cells)
    broken = run_frs(
        mapping,
        layout=layout,
        cells=cells,
        identity=frozenset({"identity.I3a"}),
    )
    assert _control(silent, "F04").status == "clear"
    assert _control(silent, "F04").confidence == "high"
    assert _control(broken, "F04").confidence == "low"


def test_i3b_caps_f13_to_low() -> None:
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
    cells = [
        cell("CF", "B2", "1"),
        cell("CF", "C2", "1"),
        cell("CF", "D2", "1"),
        cell("CF", "B3", "10"),
        cell("CF", "C3", "12"),
        cell("CF", "D3", "14"),
    ]
    broken = run_frs(
        mapping,
        layout=layout,
        cells=cells,
        identity=frozenset({"identity.I3b"}),
    )
    assert _control(broken, "F13").status == "clear"
    assert _control(broken, "F13").confidence == "low"


def test_stage_reads_identity_from_candidates(dest: Path) -> None:
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
    cells = [
        cell("BS", "B2", "100"),
        cell("BS", "C2", "216"),
        cell("BS", "D2", "216"),
        cell("P&L", "B2", "58"),
        cell("P&L", "C2", "151"),
        cell("P&L", "D2", "160"),
    ]
    write_json(dest / "mapping.json", mapping.model_dump(mode="json"))
    write_json(dest / "layout.json", layout.model_dump(mode="json"))
    write_json(
        dest / "candidates.json",
        {
            "candidates": [
                {
                    "detector": "identity.I1",
                    "cell_refs": ["BS!B2"],
                    "tags": [],
                    "payload": {},
                    "base_severity": "error",
                }
            ],
            "questions": [],
        },
    )
    (dest / "ir").mkdir(exist_ok=True)
    write_parquet(
        dest / "ir" / "cells.parquet",
        [
            ("sheet", "VARCHAR"),
            ("row", "INTEGER"),
            ("col", "INTEGER"),
            ("addr", "VARCHAR"),
            ("formula_raw", "VARCHAR"),
            ("formula_template", "VARCHAR"),
            ("unparsed", "BOOLEAN"),
            ("cached_value", "VARCHAR"),
            ("hidden", "BOOLEAN"),
            ("number_format", "VARCHAR"),
            ("comment", "VARCHAR"),
            ("ast_json", "VARCHAR"),
        ],
        [
            (
                item["sheet"],
                item["row"],
                item["col"],
                item["addr"],
                item.get("formula_raw"),
                item.get("formula_template"),
                bool(item.get("unparsed")),
                item.get("cached_value"),
                bool(item.get("hidden")),
                item.get("number_format"),
                item.get("comment"),
                item.get("ast_json"),
            )
            for item in cells
        ],
    )
    doc = frs_workbook(dest)
    assert _control(doc, "F06").confidence == "low"
