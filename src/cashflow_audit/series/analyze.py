from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from cashflow_audit.layout.models import AxisHeader, Block, Layout
from cashflow_audit.series.models import SeriesKind, SeriesOutlier

KINDS = {
    "template_change",
    "value_instead_of_formula",
    "literal_in_ast",
    "ref_shift",
    "source_sheet_change",
}
MAJORITY_THRESHOLD = 0.70
_R1C1 = re.compile(r"R(?:\[-?\d+\]|\d+)?C(?:\[-?\d+\]|\d+)?")
_BORING_NUMS = {0.0, 1.0, -1.0}


def analyze_series(layout: Layout, cells: list[dict]) -> list[SeriesOutlier]:
    index = {(c["sheet"], int(c["row"]), int(c["col"])): c for c in cells}
    outliers: list[SeriesOutlier] = []
    for sheet in layout.sheets:
        for block in sheet.blocks:
            outliers.extend(_block_outliers(sheet.name, block, index))
    return outliers


def _block_outliers(
    sheet: str, block: Block, index: dict[tuple[str, int, int], dict]
) -> list[SeriesOutlier]:
    headers = sorted(block.axis.headers, key=lambda h: h.col)
    if not headers:
        return []
    forecast_cols = [h.col for h in headers if h.role == "forecast"]
    edge_cols = {forecast_cols[0], forecast_cols[-1]} if forecast_cols else set()
    found: list[SeriesOutlier] = []
    for layout_row in block.rows:
        if layout_row.check_row:
            continue
        points = [_point(sheet, layout_row.row, header, index) for header in headers]
        trimmed = _trim(points)
        if not trimmed:
            continue
        templates = [p["template"] or "" for p in trimmed]
        formula_templates = [t for t in templates if t]
        if not formula_templates:
            continue
        majority_t, maj_count = Counter(formula_templates).most_common(1)[0]
        if maj_count / len(trimmed) < MAJORITY_THRESHOLD:
            continue
        majority_ast = next(p["ast"] for p in trimmed if p["template"] == majority_t)
        for point in trimmed:
            kind = _kind(point, majority_t, majority_ast, sheet)
            if kind is None:
                continue
            found.append(
                SeriesOutlier(
                    sheet=sheet,
                    block_id=block.block_id,
                    row=point["row"],
                    col=point["col"],
                    addr=point["addr"],
                    cell_ref=f"{sheet}!{point['addr']}",
                    kind=kind,
                    edge_period=point["col"] in edge_cols,
                    majority_template=majority_t,
                    cell_template=point["template"],
                )
            )
    return found


def _point(
    sheet: str,
    row: int,
    header: AxisHeader,
    index: dict[tuple[str, int, int], dict],
) -> dict[str, Any]:
    cell = index.get((sheet, row, header.col))
    if cell is None:
        return {
            "row": row,
            "col": header.col,
            "addr": "",
            "template": None,
            "ast": None,
            "formula": None,
            "value": None,
        }
    ast = None
    raw_ast = cell.get("ast_json")
    if raw_ast:
        ast = json.loads(raw_ast)
    return {
        "row": row,
        "col": header.col,
        "addr": cell["addr"],
        "template": cell.get("formula_template"),
        "ast": ast,
        "formula": cell.get("formula_raw"),
        "value": cell.get("cached_value"),
    }


def _trim(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    start = 0
    end = len(points)
    while start < end and _empty_or_zero(points[start]):
        start += 1
    while end > start and _empty_or_zero(points[end - 1]):
        end -= 1
    return points[start:end]


def _empty_or_zero(point: dict[str, Any]) -> bool:
    if point.get("formula"):
        return False
    value = point.get("value")
    if value is None or str(value).strip() == "":
        return True
    try:
        return float(str(value).replace(" ", "").replace(",", ".")) == 0.0
    except ValueError:
        return False


def _kind(
    point: dict[str, Any],
    majority_t: str,
    majority_ast: dict | None,
    sheet: str,
) -> SeriesKind | None:
    template = point.get("template")
    if not template:
        return "value_instead_of_formula"
    if template == majority_t:
        return None
    cell_sheets = _ast_sheets(point.get("ast"), sheet)
    maj_sheets = _ast_sheets(majority_ast, sheet)
    if cell_sheets != maj_sheets:
        return "source_sheet_change"
    if _interesting_nums(point.get("ast")) != _interesting_nums(majority_ast):
        return "literal_in_ast"
    if _refs(template) != _refs(majority_t):
        return "ref_shift"
    return "template_change"


def _ast_sheets(node: dict | None, current_sheet: str) -> frozenset[str]:
    if not node:
        return frozenset()
    op = node.get("op")
    found: set[str] = set()
    if op in {"ref", "range"}:
        sheet = node.get("sheet") or current_sheet
        if node.get("external"):
            sheet = f"[{node['external']}]{sheet}"
        found.add(sheet)
        if op == "range":
            found |= _ast_sheets(node.get("start"), current_sheet)
            found |= _ast_sheets(node.get("end"), current_sheet)
    for key in ("left", "right", "expr"):
        child = node.get(key)
        if isinstance(child, dict):
            found |= _ast_sheets(child, current_sheet)
    for arg in node.get("args") or []:
        if isinstance(arg, dict):
            found |= _ast_sheets(arg, current_sheet)
    return frozenset(found)


def _interesting_nums(node: dict | None) -> frozenset[float]:
    if not node:
        return frozenset()
    found: set[float] = set()
    if node.get("op") == "num":
        value = node.get("value")
        if isinstance(value, int | float) and float(value) not in _BORING_NUMS:
            found.add(float(value))
    for key in ("left", "right", "expr"):
        child = node.get(key)
        if isinstance(child, dict):
            found |= _interesting_nums(child)
    for arg in node.get("args") or []:
        if isinstance(arg, dict):
            found |= _interesting_nums(arg)
    return frozenset(found)


def _refs(template: str) -> tuple[str, ...]:
    return tuple(_R1C1.findall(template))
