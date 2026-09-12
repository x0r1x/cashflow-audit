from __future__ import annotations

import re
from dataclasses import dataclass

from cashflow_audit.checkers.identity import _aligned, _one_total, _value
from cashflow_audit.layout.models import Layout
from cashflow_audit.mapping.models import MappedRow, MappingDocument
from cashflow_audit.parse.a1 import format_addr

_MONTH_KEY = re.compile(r"^\d{4}-\d{2}$")
_YEAR_KEY = re.compile(r"^\d{4}$")


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
    aligned = list(_aligned(ctx, *rows))  # type: ignore[arg-type]
    native = [(key, cols) for key, cols in aligned if not _MONTH_KEY.fullmatch(key)]
    if native:
        return native
    by_year: dict[str, list[int]] = {}
    for key, cols in aligned:
        if _MONTH_KEY.fullmatch(key):
            by_year[key[:4]] = cols
    return [(year, cols) for year, cols in sorted(by_year.items())]


def month_slots(ctx: FrsCtx, *rows: MappedRow) -> list[tuple[str, list[int]]]:
    if ctx.layout is None:
        return []
    return [
        (key, cols)
        for key, cols in _aligned(ctx, *rows)  # type: ignore[arg-type]
        if _MONTH_KEY.fullmatch(key)
    ]


def cell_value(ctx: FrsCtx, row: MappedRow, col: int) -> float | None:
    return _value(ctx, row, col)  # type: ignore[arg-type]


def cell_ref(row: MappedRow, col: int) -> str:
    return f"{row.sheet}!{format_addr(col, row.row)}"


def header_col(ctx: FrsCtx, row: MappedRow, key: str) -> int | None:
    if ctx.layout is None:
        return None
    for sheet in ctx.layout.sheets:
        for block in sheet.blocks:
            if sheet.name != row.sheet or block.block_id != row.block_id:
                continue
            for header in block.axis.headers:
                if header.period_key == key:
                    return header.col
    return None


def value_at(ctx: FrsCtx, concept: str, key: str) -> tuple[float | None, str | None]:
    row = book_totals(ctx).get(concept)
    if row is None:
        return None, None
    val, refs = year_amount(ctx, row, key)
    return val, refs[0] if refs else None


def year_amount(ctx: FrsCtx, row: MappedRow, key: str) -> tuple[float | None, list[str]]:
    col = header_col(ctx, row, key)
    if col is not None:
        return cell_value(ctx, row, col), [cell_ref(row, col)]
    if not _YEAR_KEY.fullmatch(key):
        return None, []
    months = [
        (item_key, item_col)
        for item_key, _role, item_col in header_roles(ctx, row)
        if item_key.startswith(f"{key}-")
    ]
    months.sort()
    if not months:
        return None, []
    vals: list[float] = []
    refs: list[str] = []
    for _mkey, item_col in months:
        val = cell_value(ctx, row, item_col)
        if val is None:
            continue
        vals.append(val)
        refs.append(cell_ref(row, item_col))
    if not vals:
        return None, []
    if row.concept_id and row.concept_id.startswith("bs."):
        return vals[-1], [refs[-1]]
    return sum(vals), refs


def header_roles(ctx: FrsCtx, row: MappedRow) -> list[tuple[str, str, int]]:
    if ctx.layout is None:
        return []
    found: list[tuple[str, str, int]] = []
    for sheet in ctx.layout.sheets:
        for block in sheet.blocks:
            if sheet.name != row.sheet or block.block_id != row.block_id:
                continue
            for header in block.axis.headers:
                if header.role in {"historical", "forecast", "stub"}:
                    found.append((header.period_key, header.role, header.col))
    return found


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
            months = [
                header
                for header in block.axis.headers
                if header.period_key.startswith(f"{key}-")
            ]
            if months:
                months.sort(key=lambda header: header.period_key)
                return months[-1].role
    return None
