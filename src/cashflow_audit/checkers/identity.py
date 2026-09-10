from __future__ import annotations

from cashflow_audit.checkers.astutil import as_number
from cashflow_audit.checkers.context import CheckContext
from cashflow_audit.checkers.models import Candidate
from cashflow_audit.mapping.models import MappedRow, MappingQuestion
from cashflow_audit.parse.a1 import format_addr

I1_CONCEPTS = ("bs.assets_total", "bs.equity", "bs.liabilities")
I3A_CONCEPTS = ("bs.cash", "cf.cfo")
I3B_CONCEPTS = ("bs.retained_earnings", "pnl.net_income")
I5_CONCEPTS = ("pnl.gross_profit", "pnl.revenue", "pnl.cogs")
I7_CONCEPTS = ("pnl.ebit", "pnl.ebitda", "pnl.da")
_IDENTITY_PERIOD_ROLES = frozenset({"historical", "forecast", "stub"})


def detect_identities(ctx: CheckContext) -> tuple[list[Candidate], list[MappingQuestion]]:
    candidates: list[Candidate] = []
    questions: list[MappingQuestion] = []
    qn = 1
    by_block = _totals_by_block(ctx)
    book = _one_total(ctx)
    qn = _maybe_question(questions, qn, "I1", I1_CONCEPTS, book, ctx)
    for group in _groups(by_block, book, I1_CONCEPTS):
        candidates.extend(
            _i1(ctx, group["bs.assets_total"], group["bs.equity"], group["bs.liabilities"])
        )
    qn = _maybe_question(questions, qn, "I3a", I3A_CONCEPTS, book, ctx)
    for group in _groups(by_block, book, I3A_CONCEPTS):
        candidates.extend(
            _rollforward(ctx, "identity.I3a", group["bs.cash"], [group["cf.cfo"]], add=True)
        )
    qn = _maybe_question(questions, qn, "I3b", I3B_CONCEPTS, book, ctx)
    for group in _groups(by_block, book, I3B_CONCEPTS):
        div = group.get("cf.dividends") or book.get("cf.dividends")
        candidates.extend(_i3b(ctx, group["bs.retained_earnings"], group["pnl.net_income"], div))
    qn = _maybe_question(questions, qn, "I5", I5_CONCEPTS, book, ctx)
    for group in _groups(by_block, book, I5_CONCEPTS):
        candidates.extend(
            _minus(
                ctx,
                "identity.I5",
                group["pnl.gross_profit"],
                group["pnl.revenue"],
                group["pnl.cogs"],
            )
        )
    qn = _maybe_question(questions, qn, "I7", I7_CONCEPTS, book, ctx)
    for group in _groups(by_block, book, I7_CONCEPTS):
        candidates.extend(
            _minus(
                ctx,
                "identity.I7",
                group["pnl.ebit"],
                group["pnl.ebitda"],
                group["pnl.da"],
            )
        )
    return candidates, questions


def _pick_total(chosen: dict[str, MappedRow], row: MappedRow) -> None:
    prev = chosen.get(row.concept_id or "")
    if prev is None or (row.article_role == "output" and prev.article_role != "output"):
        if row.concept_id:
            chosen[row.concept_id] = row


def _totals_by_block(ctx: CheckContext) -> dict[str, dict[str, MappedRow]]:
    chosen: dict[str, dict[str, MappedRow]] = {}
    for row in ctx.mapping.rows:
        if not row.concept_id:
            continue
        _pick_total(chosen.setdefault(row.block_id, {}), row)
    return chosen


def _one_total(ctx: CheckContext) -> dict[str, MappedRow]:
    chosen: dict[str, MappedRow] = {}
    for row in ctx.mapping.rows:
        if not row.concept_id:
            continue
        _pick_total(chosen, row)
    return chosen


def _groups(
    by_block: dict[str, dict[str, MappedRow]],
    book: dict[str, MappedRow],
    needed: tuple[str, ...],
) -> list[dict[str, MappedRow]]:
    found = [bucket for bucket in by_block.values() if all(cid in bucket for cid in needed)]
    if found:
        return found
    if all(cid in book for cid in needed):
        return [book]
    return []


def _maybe_question(
    questions: list[MappingQuestion],
    qn: int,
    name: str,
    needed: tuple[str, ...],
    by_concept: dict[str, MappedRow],
    ctx: CheckContext,
) -> int:
    missing = [cid for cid in needed if cid not in by_concept]
    if not missing:
        return qn
    refs = []
    if ctx.cells:
        c0 = ctx.cells[0]
        refs = [f"{c0['sheet']}!{c0['addr']}"]
    questions.append(
        MappingQuestion(
            id=f"q_id_{qn:03d}",
            kind="identity_gap",
            prompt=f"Не хватает concept_id для {name}: {', '.join(missing)}",
            cell_refs=refs,
            options=[*missing, "unknown"],
        )
    )
    return qn + 1


def _i1(
    ctx: CheckContext,
    assets: MappedRow,
    equity: MappedRow,
    liab: MappedRow,
) -> list[Candidate]:
    found: list[Candidate] = []
    for col in _period_cols(ctx, assets, equity, liab):
        a = _value(ctx, assets, col)
        e = _value(ctx, equity, col)
        lia = _value(ctx, liab, col)
        if a is None or e is None or lia is None:
            continue
        delta = a - (e + lia)
        thresh = max(1.0, 0.001 * abs(a))
        if abs(delta) <= thresh:
            continue
        found.append(
            Candidate(
                detector="identity.I1",
                cell_refs=[
                    _cell_ref(assets, col),
                    _cell_ref(equity, col),
                    _cell_ref(liab, col),
                ],
                payload={"delta": delta, "col": col},
                base_severity="error",
            )
        )
    return found


def _minus(
    ctx: CheckContext,
    detector: str,
    result: MappedRow,
    left: MappedRow,
    right: MappedRow,
) -> list[Candidate]:
    found: list[Candidate] = []
    for col in _period_cols(ctx, result, left, right):
        got = _value(ctx, result, col)
        lhs = _value(ctx, left, col)
        rhs = _value(ctx, right, col)
        if got is None or lhs is None or rhs is None:
            continue
        delta = got - (lhs - rhs)
        thresh = max(1.0, 0.001 * max(abs(got), abs(lhs)))
        if abs(delta) <= thresh:
            continue
        found.append(
            Candidate(
                detector=detector,
                cell_refs=[
                    _cell_ref(result, col),
                    _cell_ref(left, col),
                    _cell_ref(right, col),
                ],
                payload={"delta": delta, "col": col},
                base_severity="error",
            )
        )
    return found


def _rollforward(
    ctx: CheckContext,
    detector: str,
    stock: MappedRow,
    flows: list[MappedRow],
    *,
    add: bool,
) -> list[Candidate]:
    cols = _period_cols(ctx, stock, *flows)
    found: list[Candidate] = []
    for prev, cur in zip(cols, cols[1:], strict=False):
        opening = _value(ctx, stock, prev)
        closing = _value(ctx, stock, cur)
        flow = 0.0
        ok = True
        for item in flows:
            part = _value(ctx, item, cur)
            if part is None:
                ok = False
                break
            flow += part
        if opening is None or closing is None or not ok:
            continue
        expected = opening + flow if add else opening - flow
        thresh = max(1.0, 0.001 * max(abs(opening), abs(closing)))
        if abs(closing - expected) <= thresh:
            continue
        found.append(
            Candidate(
                detector=detector,
                cell_refs=[_cell_ref(stock, prev), _cell_ref(stock, cur)],
                payload={"col": cur},
                base_severity="error",
            )
        )
    return found


def _i3b(
    ctx: CheckContext,
    retained: MappedRow,
    ni: MappedRow,
    div: MappedRow | None,
) -> list[Candidate]:
    cols = _period_cols(ctx, retained, ni)
    found: list[Candidate] = []
    for prev, cur in zip(cols, cols[1:], strict=False):
        opening = _value(ctx, retained, prev)
        closing = _value(ctx, retained, cur)
        income = _value(ctx, ni, cur)
        dividends = _value(ctx, div, cur) if div is not None else 0.0
        if opening is None or closing is None or income is None or dividends is None:
            continue
        expected = opening + income - dividends
        thresh = max(1.0, 0.001 * max(abs(opening), abs(closing)))
        if abs(closing - expected) <= thresh:
            continue
        found.append(
            Candidate(
                detector="identity.I3b",
                cell_refs=[_cell_ref(retained, prev), _cell_ref(retained, cur)],
                payload={"col": cur},
                base_severity="error",
            )
        )
    return found


def _period_cols(ctx: CheckContext, *rows: MappedRow) -> list[int]:
    cols: set[int] = set()
    wanted = {(r.sheet, r.block_id) for r in rows}
    for sheet in ctx.layout.sheets:
        for block in sheet.blocks:
            if (sheet.name, block.block_id) not in wanted:
                continue
            cols.update(
                h.col for h in block.axis.headers if h.role in _IDENTITY_PERIOD_ROLES
            )
    return sorted(cols)


def _value(ctx: CheckContext, row: MappedRow, col: int) -> float | None:
    for cell in ctx.cells:
        if cell["sheet"] == row.sheet and int(cell["row"]) == row.row and int(cell["col"]) == col:
            return as_number(cell.get("cached_value"))
    return None


def _cell_ref(row: MappedRow, col: int) -> str:
    return f"{row.sheet}!{format_addr(col, row.row)}"
