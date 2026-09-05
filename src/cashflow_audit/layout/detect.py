from __future__ import annotations

import re
from collections import defaultdict

from cashflow_audit.layout.models import (
    Axis,
    AxisHeader,
    Block,
    Layout,
    LayoutRow,
    SheetLayout,
)
from cashflow_audit.layout.periods import classify_header

_CHECK = re.compile(
    r"check|проверк|контроль|tie[- ]?out|plug\b|сход[ия]|должен",
    re.IGNORECASE,
)


def detect_layout(cells: list[dict]) -> Layout:
    by_sheet: dict[str, list[dict]] = {}
    for cell in cells:
        by_sheet.setdefault(cell["sheet"], []).append(cell)
    sheets = [
        SheetLayout(name=name, blocks=_blocks_for_sheet(name, sheet_cells))
        for name, sheet_cells in by_sheet.items()
    ]
    return Layout(sheets=sheets)


def _blocks_for_sheet(sheet: str, cells: list[dict]) -> list[Block]:
    by_row: dict[int, list[dict]] = defaultdict(list)
    for cell in cells:
        by_row[int(cell["row"])].append(cell)
    header_rows = [
        row
        for row, row_cells in sorted(by_row.items())
        if _period_count(row_cells) >= 2
    ]
    blocks: list[Block] = []
    for i, header_row in enumerate(header_rows):
        end = header_rows[i + 1] if i + 1 < len(header_rows) else max(by_row) + 1
        band_rows = [r for r in sorted(by_row) if header_row <= r < end]
        band_cells = [c for r in band_rows for c in by_row[r]]
        axis = _axis(sheet, header_row, by_row[header_row])
        label_col = _label_col(band_cells)
        data_rows = _data_rows(by_row, band_rows, header_row, label_col)
        blocks.append(
            Block(
                block_id=f"{sheet}!r{header_row}",
                label_col=label_col,
                axis=axis,
                rows=data_rows,
            )
        )
    return blocks


def _period_count(row_cells: list[dict]) -> int:
    return sum(1 for cell in row_cells if classify_header(_text(cell)) is not None)


def _axis(sheet: str, header_row: int, row_cells: list[dict]) -> Axis:
    headers: list[AxisHeader] = []
    for cell in sorted(row_cells, key=lambda c: int(c["col"])):
        hit = classify_header(_text(cell))
        if hit is None:
            continue
        headers.append(
            AxisHeader(
                col=int(cell["col"]),
                text=_text(cell) or "",
                role=hit.role,
                period_key=hit.period_key,
            )
        )
    return Axis(id=f"{sheet}!r{header_row}", row=header_row, headers=headers)


def _label_col(band_cells: list[dict]) -> int:
    candidates: list[int] = []
    for cell in band_cells:
        if cell.get("hidden"):
            continue
        text = _text(cell)
        if not text or classify_header(text) is not None or _is_number(text):
            continue
        candidates.append(int(cell["col"]))
    if candidates:
        return min(candidates)
    visible = [int(c["col"]) for c in band_cells if not c.get("hidden")]
    return min(visible) if visible else 1


def _data_rows(
    by_row: dict[int, list[dict]],
    band_rows: list[int],
    header_row: int,
    label_col: int,
) -> list[LayoutRow]:
    out: list[LayoutRow] = []
    for row_n in band_rows:
        if row_n == header_row:
            continue
        label_cell = _cell_at(by_row[row_n], label_col)
        if label_cell is None:
            continue
        label = _text(label_cell)
        if not label:
            continue
        indent = _indent(label)
        parent_row = None
        for prev in reversed(out):
            if prev.indent < indent:
                parent_row = prev.row
                break
        out.append(
            LayoutRow(
                row=row_n,
                label=label.strip(),
                parent_row=parent_row,
                indent=indent,
                check_row=bool(_CHECK.search(label)),
                hidden=bool(label_cell.get("hidden")),
            )
        )
    return out


def _cell_at(row_cells: list[dict], col: int) -> dict | None:
    for cell in row_cells:
        if int(cell["col"]) == col:
            return cell
    return None


def _text(cell: dict) -> str | None:
    value = cell.get("cached_value")
    if value is None:
        return None
    text = str(value)
    if not text.strip():
        return None
    return text


def _is_number(text: str) -> bool:
    try:
        float(text.replace(" ", "").replace(",", "."))
    except ValueError:
        return False
    return True


def _indent(label: str) -> int:
    stripped = label.lstrip(" \t")
    return len(label) - len(stripped)
