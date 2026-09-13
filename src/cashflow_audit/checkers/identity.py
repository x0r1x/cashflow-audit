from __future__ import annotations

from cashflow_audit.checkers.astutil import as_number
from cashflow_audit.checkers.context import CheckContext
from cashflow_audit.checkers.models import Candidate
from cashflow_audit.mapping.models import MappedRow, MappingQuestion
from cashflow_audit.parse.a1 import format_addr

I1_CONCEPTS = ("bs.assets_total", "bs.equity", "bs.liabilities")
I3A_CONCEPTS = ("bs.cash", "cf.fcf")
I3B_CONCEPTS = ("bs.retained_earnings", "pnl.net_income")
I5_CONCEPTS = ("pnl.gross_profit", "pnl.revenue", "pnl.cogs")
I7_CONCEPTS = ("pnl.ebit", "pnl.ebitda", "pnl.da")
I8_CONCEPTS = ("pnl.da", "cf.da")
I8B_STOCK = ("bs.ppe",)
I8B_FLOWS = ("cf.capex", "pnl.da")
I9_NEED = ("pnl.net_income", "cf.cfo")
I9_NICE = ("pnl.da", "bs.ar", "bs.inventory", "bs.ap")
I10_STOCK = ("bs.debt",)
I10_FLOWS = ("cf.drawdown", "cf.repayment")
I11_CONCEPTS = ("pnl.interest", "bs.debt", "pnl.interest_rate")
I12_CONCEPTS = ("pnl.tax", "pnl.net_income", "pnl.tax_rate")
_IDENTITY_PERIOD_ROLES = frozenset({"historical", "forecast", "stub"})
_IDENTITY_SNAPSHOT_ROLES = _IDENTITY_PERIOD_ROLES | {"scenario"}


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
    qn = _maybe_question(questions, qn, "I3a", ("bs.cash",), book, ctx)
    for group in _groups(by_block, book, I3A_CONCEPTS):
        candidates.extend(
            _rollforward(ctx, "identity.I3a", group["bs.cash"], [group["cf.fcf"]], add=True)
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
    qn = _maybe_question(questions, qn, "I9", I9_NEED, book, ctx)
    if all(cid in book for cid in I9_NEED) and not any(cid in book for cid in I9_NICE):
        qn = _maybe_question(questions, qn, "I9", I9_NICE, book, ctx)
    for group in _groups(by_block, book, I9_NEED):
        da = group.get("pnl.da") or book.get("pnl.da")
        ar = group.get("bs.ar") or book.get("bs.ar")
        inv = group.get("bs.inventory") or book.get("bs.inventory")
        ap = group.get("bs.ap") or book.get("bs.ap")
        if da is None and ar is None and inv is None and ap is None:
            continue
        candidates.extend(
            _i9(
                ctx,
                group["pnl.net_income"],
                group["cf.cfo"],
                da,
                ar,
                inv,
                ap,
            )
        )
    qn = _maybe_question(questions, qn, "I10", I10_STOCK, book, ctx)
    if "bs.debt" in book and not any(cid in book for cid in I10_FLOWS):
        qn = _maybe_question(questions, qn, "I10", I10_FLOWS, book, ctx)
    for group in _groups(by_block, book, I10_STOCK):
        draw = group.get("cf.drawdown") or book.get("cf.drawdown")
        repay = group.get("cf.repayment") or book.get("cf.repayment")
        if draw is None and repay is None:
            continue
        candidates.extend(_i10(ctx, group["bs.debt"], draw, repay))
    qn = _maybe_question(questions, qn, "I11", I11_CONCEPTS, book, ctx)
    for group in _groups(by_block, book, I11_CONCEPTS):
        candidates.extend(
            _i11(
                ctx,
                group["pnl.interest"],
                group["bs.debt"],
                group["pnl.interest_rate"],
            )
        )
    qn = _maybe_question(questions, qn, "I12", I12_CONCEPTS, book, ctx)
    for group in _groups(by_block, book, I12_CONCEPTS):
        candidates.extend(
            _i12(
                ctx,
                group["pnl.tax"],
                group["pnl.net_income"],
                group["pnl.tax_rate"],
            )
        )
    if "pnl.da" in book and "cf.da" not in book:
        qn = _maybe_question(questions, qn, "I8", ("cf.da",), book, ctx)
    elif "cf.da" in book and "pnl.da" not in book:
        qn = _maybe_question(questions, qn, "I8", ("pnl.da",), book, ctx)
    for group in _groups(by_block, book, I8_CONCEPTS):
        candidates.extend(_i8(ctx, group["pnl.da"], group["cf.da"]))
    if "bs.ppe" in book and not any(cid in book for cid in I8B_FLOWS):
        qn = _maybe_question(questions, qn, "I8b", I8B_FLOWS, book, ctx)
    elif "bs.ppe" not in book and any(cid in book for cid in I8B_FLOWS):
        qn = _maybe_question(questions, qn, "I8b", I8B_STOCK, book, ctx)
    for group in _groups(by_block, book, I8B_STOCK):
        capex = group.get("cf.capex") or book.get("cf.capex")
        da = group.get("pnl.da") or book.get("pnl.da")
        if capex is None and da is None:
            continue
        candidates.extend(_i8b(ctx, group["bs.ppe"], capex, da))
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
    for key, cols in _aligned(ctx, assets, equity, liab, roles=_IDENTITY_SNAPSHOT_ROLES):
        a = _value(ctx, assets, cols[0])
        e = _value(ctx, equity, cols[1])
        lia = _value(ctx, liab, cols[2])
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
                    _cell_ref(assets, cols[0]),
                    _cell_ref(equity, cols[1]),
                    _cell_ref(liab, cols[2]),
                ],
                payload={"delta": delta, "col": cols[0], "period_key": key},
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
    for key, cols in _aligned(ctx, result, left, right, roles=_IDENTITY_SNAPSHOT_ROLES):
        got = _value(ctx, result, cols[0])
        lhs = _value(ctx, left, cols[1])
        rhs = _value(ctx, right, cols[2])
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
                    _cell_ref(result, cols[0]),
                    _cell_ref(left, cols[1]),
                    _cell_ref(right, cols[2]),
                ],
                payload={"delta": delta, "col": cols[0], "period_key": key},
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
    slots = _aligned(ctx, stock, *flows)
    found: list[Candidate] = []
    for prev, cur in zip(slots, slots[1:], strict=False):
        _pkey, prev_cols = prev
        key, cur_cols = cur
        opening = _value(ctx, stock, prev_cols[0])
        closing = _value(ctx, stock, cur_cols[0])
        flow = 0.0
        ok = True
        for index, item in enumerate(flows):
            part = _value(ctx, item, cur_cols[index + 1])
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
        refs = [_cell_ref(stock, prev_cols[0]), _cell_ref(stock, cur_cols[0])]
        for index, item in enumerate(flows):
            refs.append(_cell_ref(item, cur_cols[index + 1]))
        found.append(
            Candidate(
                detector=detector,
                cell_refs=refs,
                payload={"col": cur_cols[0], "period_key": key},
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
    slots = _aligned(ctx, retained, ni)
    div_cols = _axis_cols(ctx, div) if div is not None else {}
    found: list[Candidate] = []
    for prev, cur in zip(slots, slots[1:], strict=False):
        _pkey, prev_cols = prev
        key, cur_cols = cur
        opening = _value(ctx, retained, prev_cols[0])
        closing = _value(ctx, retained, cur_cols[0])
        income = _value(ctx, ni, cur_cols[1])
        dividends = 0.0
        if div is not None and key in div_cols:
            part = _value(ctx, div, div_cols[key])
            if part is None:
                continue
            dividends = part
        if opening is None or closing is None or income is None:
            continue
        expected = opening + income - dividends
        thresh = max(1.0, 0.001 * max(abs(opening), abs(closing)))
        if abs(closing - expected) <= thresh:
            continue
        refs = [
            _cell_ref(retained, prev_cols[0]),
            _cell_ref(retained, cur_cols[0]),
            _cell_ref(ni, cur_cols[1]),
        ]
        if div is not None and key in div_cols:
            refs.append(_cell_ref(div, div_cols[key]))
        found.append(
            Candidate(
                detector="identity.I3b",
                cell_refs=refs,
                payload={"col": cur_cols[0], "period_key": key},
                base_severity="error",
            )
        )
    return found


def _i10(
    ctx: CheckContext,
    debt: MappedRow,
    draw: MappedRow | None,
    repay: MappedRow | None,
) -> list[Candidate]:
    slots = _aligned(ctx, debt)
    draw_cols = _axis_cols(ctx, draw) if draw is not None else {}
    repay_cols = _axis_cols(ctx, repay) if repay is not None else {}
    found: list[Candidate] = []
    for prev, cur in zip(slots, slots[1:], strict=False):
        _pkey, prev_cols = prev
        key, cur_cols = cur
        opening = _value(ctx, debt, prev_cols[0])
        closing = _value(ctx, debt, cur_cols[0])
        drawn = 0.0
        repaid = 0.0
        if draw is not None and key in draw_cols:
            part = _value(ctx, draw, draw_cols[key])
            if part is None:
                continue
            drawn = part
        if repay is not None and key in repay_cols:
            part = _value(ctx, repay, repay_cols[key])
            if part is None:
                continue
            repaid = part
        if opening is None or closing is None:
            continue
        expected = opening + drawn - repaid
        thresh = max(1.0, 0.001 * max(abs(opening), abs(closing)))
        if abs(closing - expected) <= thresh:
            continue
        refs = [_cell_ref(debt, prev_cols[0]), _cell_ref(debt, cur_cols[0])]
        if draw is not None and key in draw_cols:
            refs.append(_cell_ref(draw, draw_cols[key]))
        if repay is not None and key in repay_cols:
            refs.append(_cell_ref(repay, repay_cols[key]))
        found.append(
            Candidate(
                detector="identity.I10",
                cell_refs=refs,
                payload={"col": cur_cols[0], "period_key": key, "delta": closing - expected},
                base_severity="error",
            )
        )
    return found


def _i9(
    ctx: CheckContext,
    ni: MappedRow,
    cfo: MappedRow,
    da: MappedRow | None,
    ar: MappedRow | None,
    inv: MappedRow | None,
    ap: MappedRow | None,
) -> list[Candidate]:
    slots = _aligned(ctx, ni, cfo)
    da_cols = _axis_cols(ctx, da) if da is not None else {}
    found: list[Candidate] = []
    for prev, cur in zip(slots, slots[1:], strict=False):
        pkey, _prev_cols = prev
        key, cur_cols = cur
        income = _value(ctx, ni, cur_cols[0])
        got = _value(ctx, cfo, cur_cols[1])
        if income is None or got is None:
            continue
        da_v = 0.0
        refs = [_cell_ref(ni, cur_cols[0]), _cell_ref(cfo, cur_cols[1])]
        if da is not None and key in da_cols:
            part = _value(ctx, da, da_cols[key])
            if part is None:
                continue
            da_v = part
            refs.append(_cell_ref(da, da_cols[key]))
        d_ar, ar_refs = _stock_delta(ctx, ar, pkey, key)
        d_inv, inv_refs = _stock_delta(ctx, inv, pkey, key)
        d_ap, ap_refs = _stock_delta(ctx, ap, pkey, key)
        if d_ar is None or d_inv is None or d_ap is None:
            continue
        expected = income + da_v - d_ar - d_inv + d_ap
        thresh = max(1.0, 0.001 * max(abs(got), abs(income)))
        if abs(got - expected) <= thresh:
            continue
        refs.extend(ar_refs)
        refs.extend(inv_refs)
        refs.extend(ap_refs)
        found.append(
            Candidate(
                detector="identity.I9",
                cell_refs=refs,
                payload={"col": cur_cols[1], "period_key": key, "delta": got - expected},
                base_severity="error",
            )
        )
    return found


def _stock_delta(
    ctx: CheckContext,
    row: MappedRow | None,
    prev_key: str,
    key: str,
) -> tuple[float | None, list[str]]:
    if row is None:
        return 0.0, []
    cols = _axis_cols(ctx, row)
    if prev_key not in cols or key not in cols:
        return None, []
    before = _value(ctx, row, cols[prev_key])
    after = _value(ctx, row, cols[key])
    if before is None or after is None:
        return None, []
    return after - before, [_cell_ref(row, cols[prev_key]), _cell_ref(row, cols[key])]


def _i11(
    ctx: CheckContext,
    interest: MappedRow,
    debt: MappedRow,
    rate: MappedRow,
) -> list[Candidate]:
    slots = _aligned(ctx, interest, debt, rate)
    found: list[Candidate] = []
    for prev, cur in zip(slots, slots[1:], strict=False):
        _pkey, prev_cols = prev
        key, cur_cols = cur
        got = _value(ctx, interest, cur_cols[0])
        opening = _value(ctx, debt, prev_cols[1])
        closing = _value(ctx, debt, cur_cols[1])
        raw_rate = _value(ctx, rate, cur_cols[2])
        if got is None or opening is None or closing is None or raw_rate is None:
            continue
        expected = _as_rate(raw_rate) * (abs(opening) + abs(closing)) / 2.0
        thresh = max(1.0, 0.001 * max(abs(got), abs(expected)))
        if abs(abs(got) - abs(expected)) <= thresh:
            continue
        found.append(
            Candidate(
                detector="identity.I11",
                cell_refs=[
                    _cell_ref(interest, cur_cols[0]),
                    _cell_ref(debt, prev_cols[1]),
                    _cell_ref(debt, cur_cols[1]),
                    _cell_ref(rate, cur_cols[2]),
                ],
                payload={"col": cur_cols[0], "period_key": key, "delta": abs(got) - abs(expected)},
                base_severity="error",
            )
        )
    return found


def _i12(
    ctx: CheckContext,
    tax: MappedRow,
    ni: MappedRow,
    rate: MappedRow,
) -> list[Candidate]:
    found: list[Candidate] = []
    for key, cols in _aligned(ctx, tax, ni, rate, roles=_IDENTITY_SNAPSHOT_ROLES):
        got = _value(ctx, tax, cols[0])
        income = _value(ctx, ni, cols[1])
        raw_rate = _value(ctx, rate, cols[2])
        if got is None or income is None or raw_rate is None:
            continue
        expected = _as_rate(raw_rate) * (abs(income) + abs(got))
        thresh = max(1.0, 0.001 * max(abs(got), abs(expected)))
        if abs(abs(got) - abs(expected)) <= thresh:
            continue
        found.append(
            Candidate(
                detector="identity.I12",
                cell_refs=[
                    _cell_ref(tax, cols[0]),
                    _cell_ref(ni, cols[1]),
                    _cell_ref(rate, cols[2]),
                ],
                payload={"col": cols[0], "period_key": key, "delta": abs(got) - abs(expected)},
                base_severity="error",
            )
        )
    return found


def _i8(
    ctx: CheckContext,
    pnl_da: MappedRow,
    cf_da: MappedRow,
) -> list[Candidate]:
    found: list[Candidate] = []
    for key, cols in _aligned(ctx, pnl_da, cf_da, roles=_IDENTITY_SNAPSHOT_ROLES):
        left = _value(ctx, pnl_da, cols[0])
        right = _value(ctx, cf_da, cols[1])
        if left is None or right is None:
            continue
        thresh = max(1.0, 0.001 * max(abs(left), abs(right)))
        if abs(abs(left) - abs(right)) <= thresh:
            continue
        found.append(
            Candidate(
                detector="identity.I8",
                cell_refs=[
                    _cell_ref(pnl_da, cols[0]),
                    _cell_ref(cf_da, cols[1]),
                ],
                payload={"col": cols[0], "period_key": key, "delta": abs(left) - abs(right)},
                base_severity="error",
            )
        )
    return found


def _i8b(
    ctx: CheckContext,
    ppe: MappedRow,
    capex: MappedRow | None,
    da: MappedRow | None,
) -> list[Candidate]:
    slots = _aligned(ctx, ppe)
    capex_cols = _axis_cols(ctx, capex) if capex is not None else {}
    da_cols = _axis_cols(ctx, da) if da is not None else {}
    found: list[Candidate] = []
    for prev, cur in zip(slots, slots[1:], strict=False):
        _pkey, prev_cols = prev
        key, cur_cols = cur
        opening = _value(ctx, ppe, prev_cols[0])
        closing = _value(ctx, ppe, cur_cols[0])
        spent = 0.0
        depreciated = 0.0
        if capex is not None and key in capex_cols:
            part = _value(ctx, capex, capex_cols[key])
            if part is None:
                continue
            spent = part
        if da is not None and key in da_cols:
            part = _value(ctx, da, da_cols[key])
            if part is None:
                continue
            depreciated = part
        if opening is None or closing is None:
            continue
        expected = opening + abs(spent) - abs(depreciated)
        thresh = max(1.0, 0.001 * max(abs(opening), abs(closing)))
        if abs(closing - expected) <= thresh:
            continue
        refs = [_cell_ref(ppe, prev_cols[0]), _cell_ref(ppe, cur_cols[0])]
        if capex is not None and key in capex_cols:
            refs.append(_cell_ref(capex, capex_cols[key]))
        if da is not None and key in da_cols:
            refs.append(_cell_ref(da, da_cols[key]))
        found.append(
            Candidate(
                detector="identity.I8b",
                cell_refs=refs,
                payload={"col": cur_cols[0], "period_key": key, "delta": closing - expected},
                base_severity="error",
            )
        )
    return found


def _as_rate(raw: float) -> float:
    if abs(raw) > 1.0:
        return raw / 100.0
    return raw


_ROLE_RANK = {"historical": 0, "stub": 1, "forecast": 2}


def _axis_cols(
    ctx: CheckContext,
    row: MappedRow,
    roles: frozenset[str] | None = None,
) -> dict[str, int]:
    allowed = roles if roles is not None else _IDENTITY_PERIOD_ROLES
    found: dict[str, int] = {}
    rank: dict[str, int] = {}
    for sheet in ctx.layout.sheets:
        for block in sheet.blocks:
            if sheet.name != row.sheet or block.block_id != row.block_id:
                continue
            for header in block.axis.headers:
                if header.role not in allowed:
                    continue
                key = header.period_key
                weight = _ROLE_RANK.get(header.role, 9)
                if key not in rank or weight < rank[key]:
                    found[key] = header.col
                    rank[key] = weight
    return found


def _aligned(
    ctx: CheckContext,
    *rows: MappedRow,
    roles: frozenset[str] | None = None,
) -> list[tuple[str, list[int]]]:
    maps = [_axis_cols(ctx, row, roles=roles) for row in rows]
    if not maps or any(not item for item in maps):
        return []
    keys = set(maps[0])
    for item in maps[1:]:
        keys &= set(item)
    return [(key, [item[key] for item in maps]) for key in sorted(keys)]


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
