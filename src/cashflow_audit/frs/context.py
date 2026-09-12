from __future__ import annotations

import re
from dataclasses import dataclass

from cashflow_audit.checkers.identity import _aligned, _one_total, _value
from cashflow_audit.layout.models import Layout
from cashflow_audit.mapping.models import MappedRow, MappingDocument
from cashflow_audit.parse.a1 import format_addr

_MONTH_KEY = re.compile(r"^\d{4}-\d{2}$")


@dataclass
class FrsCtx:
    mapping: MappingDocument
    layout: Layout | None
    cells: list[dict]


def book_totals(ctx: FrsCtx) -> dict[str, MappedRow]:
    return _one_total(ctx)  # type: ignore[arg-type]


def year_slots(ctx: FrsCtx, *rows: MappedRow) -> list[tuple[str, list[int]]]:
    if ctx.layout is None:
        return []
    return [
        (key, cols)
        for key, cols in _aligned(ctx, *rows)  # type: ignore[arg-type]
        if not _MONTH_KEY.fullmatch(key)
    ]


def cell_value(ctx: FrsCtx, row: MappedRow, col: int) -> float | None:
    return _value(ctx, row, col)  # type: ignore[arg-type]


def cell_ref(row: MappedRow, col: int) -> str:
    return f"{row.sheet}!{format_addr(col, row.row)}"


def period_role(ctx: FrsCtx, row: MappedRow, key: str) -> str | None:
    if ctx.layout is None:
        return None
    for sheet in ctx.layout.sheets:
        for block in sheet.blocks:
            if sheet.name != row.sheet or block.block_id != row.block_id:
                continue
            for header in block.axis.headers:
                if header.period_key == key:
                    return header.role
    return None
