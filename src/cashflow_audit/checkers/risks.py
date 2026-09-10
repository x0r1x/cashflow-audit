from __future__ import annotations

from cashflow_audit.checkers.context import CheckContext
from cashflow_audit.checkers.identity import (
    _cell_ref,
    _groups,
    _one_total,
    _period_cols,
    _totals_by_block,
    _value,
)
from cashflow_audit.checkers.models import Candidate
from cashflow_audit.mapping.models import MappedRow

YOY_DROP = 0.10
MARGIN_DROP = 0.05
NEG_STREAK = 2
DEBT_EBITDA_JUMP = 1.0
ICR_FLOOR = 1.5
AGGRESSIVE_LIFT = 0.15


def detect_risks(ctx: CheckContext) -> list[Candidate]:
    by_block = _totals_by_block(ctx)
    book = _one_total(ctx)
    found: list[Candidate] = []
    found.extend(_yoy(ctx, by_block, book, "pnl.revenue", "risk.revenue_drop"))
    found.extend(_yoy(ctx, by_block, book, "pnl.ebitda", "risk.ebitda_drop"))
    found.extend(_ebitda_margin(ctx, by_block, book))
    found.extend(_streak_negative(ctx, by_block, book, "pnl.ebitda", "risk.negative_ebitda"))
    found.extend(_streak_negative(ctx, by_block, book, "pnl.net_income", "risk.negative_ebitda"))
    found.extend(_negative_flow(ctx, by_block, book, "cf.cfo", "risk.negative_cfo"))
    found.extend(_cash_negative(ctx, by_block, book))
    found.extend(_debt_load(ctx, by_block, book))
    found.extend(_icr(ctx, by_block, book))
    found.extend(_aggressive_growth(ctx, by_block, book))
    found.extend(_dividends_vs_cfo(ctx, by_block, book))
    return found


def _rows(
    by_block: dict[str, dict[str, MappedRow]],
    book: dict[str, MappedRow],
    *concepts: str,
) -> list[dict[str, MappedRow]]:
    return _groups(by_block, book, concepts)


def _yoy(
    ctx: CheckContext,
    by_block: dict[str, dict[str, MappedRow]],
    book: dict[str, MappedRow],
    concept: str,
    detector: str,
) -> list[Candidate]:
    found: list[Candidate] = []
    for group in _rows(by_block, book, concept):
        row = group[concept]
        cols = _period_cols(ctx, row)
        for prev, cur in zip(cols, cols[1:], strict=False):
            before = _value(ctx, row, prev)
            after = _value(ctx, row, cur)
            if before is None or after is None or before == 0.0:
                continue
            change = after / before - 1.0
            if change > -YOY_DROP:
                continue
            found.append(
                _risk(
                    detector,
                    [_cell_ref(row, prev), _cell_ref(row, cur)],
                    {"col": cur, "change": change},
                )
            )
    return found


def _ebitda_margin(
    ctx: CheckContext,
    by_block: dict[str, dict[str, MappedRow]],
    book: dict[str, MappedRow],
) -> list[Candidate]:
    found: list[Candidate] = []
    for group in _rows(by_block, book, "pnl.ebitda", "pnl.revenue"):
        ebitda, rev = group["pnl.ebitda"], group["pnl.revenue"]
        cols = _period_cols(ctx, ebitda, rev)
        for prev, cur in zip(cols, cols[1:], strict=False):
            e0, r0 = _value(ctx, ebitda, prev), _value(ctx, rev, prev)
            e1, r1 = _value(ctx, ebitda, cur), _value(ctx, rev, cur)
            if None in (e0, r0, e1, r1) or r0 == 0.0 or r1 == 0.0:
                continue
            drop = e1 / r1 - e0 / r0
            if drop > -MARGIN_DROP:
                continue
            found.append(
                _risk(
                    "risk.ebitda_drop",
                    [_cell_ref(ebitda, cur), _cell_ref(rev, cur)],
                    {"col": cur, "margin_drop": drop},
                )
            )
    return found


def _streak_negative(
    ctx: CheckContext,
    by_block: dict[str, dict[str, MappedRow]],
    book: dict[str, MappedRow],
    concept: str,
    detector: str,
) -> list[Candidate]:
    found: list[Candidate] = []
    for group in _rows(by_block, book, concept):
        row = group[concept]
        cols = _period_cols(ctx, row)
        run: list[int] = []
        for col in cols:
            val = _value(ctx, row, col)
            if val is not None and val < 0.0:
                run.append(col)
            else:
                if len(run) >= NEG_STREAK:
                    found.append(
                        _risk(
                            detector,
                            [_cell_ref(row, c) for c in run],
                            {"cols": list(run)},
                        )
                    )
                run = []
        if len(run) >= NEG_STREAK:
            found.append(
                _risk(detector, [_cell_ref(row, c) for c in run], {"cols": list(run)})
            )
    return found


def _negative_flow(
    ctx: CheckContext,
    by_block: dict[str, dict[str, MappedRow]],
    book: dict[str, MappedRow],
    concept: str,
    detector: str,
) -> list[Candidate]:
    found: list[Candidate] = []
    for group in _rows(by_block, book, concept):
        row = group[concept]
        for col in _period_cols(ctx, row):
            val = _value(ctx, row, col)
            if val is None or val >= 0.0:
                continue
            found.append(_risk(detector, [_cell_ref(row, col)], {"col": col, "value": val}))
    return found


def _cash_negative(
    ctx: CheckContext,
    by_block: dict[str, dict[str, MappedRow]],
    book: dict[str, MappedRow],
) -> list[Candidate]:
    return _negative_flow(ctx, by_block, book, "bs.cash", "risk.cash_negative")


def _debt_load(
    ctx: CheckContext,
    by_block: dict[str, dict[str, MappedRow]],
    book: dict[str, MappedRow],
) -> list[Candidate]:
    found: list[Candidate] = []
    for group in _rows(by_block, book, "bs.debt", "pnl.ebitda"):
        debt, ebitda = group["bs.debt"], group["pnl.ebitda"]
        cols = _period_cols(ctx, debt, ebitda)
        ratios: list[tuple[int, float]] = []
        for col in cols:
            d, e = _value(ctx, debt, col), _value(ctx, ebitda, col)
            if d is None or e is None or e <= 0.0:
                continue
            ratios.append((col, d / e))
        for (_c0, r0), (c1, r1) in zip(ratios, ratios[1:], strict=False):
            if r1 - r0 < DEBT_EBITDA_JUMP:
                continue
            found.append(
                _risk(
                    "risk.debt_load",
                    [_cell_ref(debt, c1), _cell_ref(ebitda, c1)],
                    {"col": c1, "ratio": r1, "prev_ratio": r0},
                )
            )
    return found


def _icr(
    ctx: CheckContext,
    by_block: dict[str, dict[str, MappedRow]],
    book: dict[str, MappedRow],
) -> list[Candidate]:
    found: list[Candidate] = []
    for group in _rows(by_block, book, "pnl.ebit", "pnl.interest"):
        ebit, interest = group["pnl.ebit"], group["pnl.interest"]
        for col in _period_cols(ctx, ebit, interest):
            e, i = _value(ctx, ebit, col), _value(ctx, interest, col)
            if e is None or i is None or i == 0.0:
                continue
            icr = e / abs(i)
            if icr >= ICR_FLOOR:
                continue
            found.append(
                _risk(
                    "risk.icr",
                    [_cell_ref(ebit, col), _cell_ref(interest, col)],
                    {"col": col, "icr": icr},
                )
            )
    return found


def _aggressive_growth(
    ctx: CheckContext,
    by_block: dict[str, dict[str, MappedRow]],
    book: dict[str, MappedRow],
) -> list[Candidate]:
    found: list[Candidate] = []
    for group in _rows(by_block, book, "pnl.revenue"):
        row = group["pnl.revenue"]
        hist = _role_cols(ctx, row, "historical")
        forecast = _role_cols(ctx, row, "forecast")
        hist_g = _mean_yoy(ctx, row, hist)
        fcst_g = _mean_yoy(ctx, row, forecast)
        if hist_g is None or fcst_g is None:
            continue
        if fcst_g - hist_g < AGGRESSIVE_LIFT:
            continue
        refs = [_cell_ref(row, c) for c in hist + forecast]
        found.append(
            _risk(
                "risk.aggressive_growth",
                refs,
                {"hist_growth": hist_g, "forecast_growth": fcst_g},
            )
        )
    return found


def _dividends_vs_cfo(
    ctx: CheckContext,
    by_block: dict[str, dict[str, MappedRow]],
    book: dict[str, MappedRow],
) -> list[Candidate]:
    found: list[Candidate] = []
    for group in _rows(by_block, book, "cf.dividends", "cf.cfo"):
        div, cfo = group["cf.dividends"], group["cf.cfo"]
        for col in _period_cols(ctx, div, cfo):
            d, c = _value(ctx, div, col), _value(ctx, cfo, col)
            if d is None or c is None or d <= 0.0 or c >= d:
                continue
            found.append(
                _risk(
                    "risk.dividends_vs_cfo",
                    [_cell_ref(div, col), _cell_ref(cfo, col)],
                    {"col": col},
                )
            )
    return found


def _role_cols(ctx: CheckContext, row: MappedRow, role: str) -> list[int]:
    cols: list[int] = []
    for sheet in ctx.layout.sheets:
        for block in sheet.blocks:
            if sheet.name != row.sheet or block.block_id != row.block_id:
                continue
            for header in block.axis.headers:
                if header.role == role:
                    cols.append(header.col)
    return sorted(cols)


def _mean_yoy(ctx: CheckContext, row: MappedRow, cols: list[int]) -> float | None:
    changes: list[float] = []
    for prev, cur in zip(cols, cols[1:], strict=False):
        before = _value(ctx, row, prev)
        after = _value(ctx, row, cur)
        if before is None or after is None or before == 0.0:
            continue
        changes.append(after / before - 1.0)
    if not changes:
        return None
    return sum(changes) / len(changes)


def _risk(detector: str, refs: list[str], payload: dict) -> Candidate:
    return Candidate(
        detector=detector,
        cell_refs=refs,
        payload=payload,
        base_severity="risk",
    )
