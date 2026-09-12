from __future__ import annotations

from cashflow_audit.frs.context import (
    FrsCtx,
    book_totals,
    cell_ref,
    cell_value,
    month_slots,
    period_role,
    value_at,
    year_slots,
)
from cashflow_audit.frs.models import ControlResult, FrsIssue
from cashflow_audit.mapping.models import MappedRow

YOY_DROP = 0.10
MARGIN_DROP = 0.05
NEG_STREAK = 2
AGGRESSIVE_LIFT = 0.15
CLIFF_PP = 0.15
DEBT_EBITDA_JUMP = 1.0
DEBT_EBITDA_CEILING = 4.0
ICR_FLOOR = 1.5
ICR_HIGH = 1.0
CASH_FLAT = 0.01


def handle_f01(ctx: FrsCtx, spec_id: str, name: str) -> tuple[ControlResult, FrsIssue | None]:
    row = book_totals(ctx).get("pnl.revenue")
    if row is None:
        return _na(spec_id, name), None
    slots = year_slots(ctx, row)
    flagged_refs: list[str] = []
    metrics: dict = {}
    prev_yoy: float | None = None
    for prev, cur in zip(slots, slots[1:], strict=False):
        pkey, pcols = prev
        ckey, ccols = cur
        before = cell_value(ctx, row, pcols[0])
        after = cell_value(ctx, row, ccols[0])
        if before is None or after is None or before == 0.0:
            continue
        change = after / before - 1.0
        role = period_role(ctx, row, ckey)
        if role == "forecast" and change <= -YOY_DROP:
            flagged_refs = [cell_ref(row, pcols[0]), cell_ref(row, ccols[0])]
            metrics = {"change": change, "period_key": ckey}
            break
        if (
            period_role(ctx, row, pkey) == "historical"
            and role == "forecast"
            and prev_yoy is not None
            and abs(change - prev_yoy) >= CLIFF_PP
        ):
            flagged_refs = [cell_ref(row, pcols[0]), cell_ref(row, ccols[0])]
            metrics = {"change": change, "prev_yoy": prev_yoy, "period_key": ckey}
            break
        prev_yoy = change
    return _finish(spec_id, name, flagged_refs, metrics, "assumptions", "low")


def handle_f02(ctx: FrsCtx, spec_id: str, name: str) -> tuple[ControlResult, FrsIssue | None]:
    totals = book_totals(ctx)
    ebitda, rev = totals.get("pnl.ebitda"), totals.get("pnl.revenue")
    if ebitda is None or rev is None:
        return _na(spec_id, name), None
    slots = year_slots(ctx, ebitda, rev)
    for prev, cur in zip(slots, slots[1:], strict=False):
        _pkey, pcols = prev
        ckey, ccols = cur
        if period_role(ctx, ebitda, ckey) != "forecast":
            continue
        e0 = cell_value(ctx, ebitda, pcols[0])
        e1 = cell_value(ctx, ebitda, ccols[0])
        r0 = cell_value(ctx, rev, pcols[1])
        r1 = cell_value(ctx, rev, ccols[1])
        refs = [
            cell_ref(ebitda, pcols[0]),
            cell_ref(ebitda, ccols[0]),
            cell_ref(rev, ccols[1]),
        ]
        if e0 is not None and e1 is not None and e0 != 0.0 and e1 / e0 - 1.0 <= -YOY_DROP:
            return _finish(
                spec_id,
                name,
                refs,
                {"change": e1 / e0 - 1.0, "period_key": ckey},
                "assumptions",
                "low",
            )
        if None not in (e0, e1, r0, r1) and r0 not in (0.0, None) and r1 not in (0.0, None):
            drop = e1 / r1 - e0 / r0  # type: ignore[operator]
            if drop <= -MARGIN_DROP:
                return _finish(
                    spec_id,
                    name,
                    refs,
                    {"margin_drop": drop, "period_key": ckey},
                    "assumptions",
                    "low",
                )
    return _finish(spec_id, name, [], {}, "assumptions", "low")


def handle_f03(ctx: FrsCtx, spec_id: str, name: str) -> tuple[ControlResult, FrsIssue | None]:
    totals = book_totals(ctx)
    row = totals.get("pnl.ebitda") or totals.get("pnl.net_income")
    if row is None:
        return _na(spec_id, name), None
    slots = year_slots(ctx, row)
    run: list[tuple[str, int]] = []
    for key, cols in slots:
        if period_role(ctx, row, key) != "forecast":
            run = []
            continue
        val = cell_value(ctx, row, cols[0])
        if val is not None and val < 0.0:
            run.append((key, cols[0]))
            if len(run) >= NEG_STREAK:
                refs = [cell_ref(row, col) for _k, col in run]
                return _finish(
                    spec_id,
                    name,
                    refs,
                    {"cols": [k for k, _c in run]},
                    "assumptions",
                    "medium",
                )
        else:
            run = []
    return _finish(spec_id, name, [], {}, "assumptions", "medium")


def handle_f11(ctx: FrsCtx, spec_id: str, name: str) -> tuple[ControlResult, FrsIssue | None]:
    row = book_totals(ctx).get("pnl.revenue")
    if row is None:
        return _na(spec_id, name), None
    slots = year_slots(ctx, row)
    hist = [(k, c[0]) for k, c in slots if period_role(ctx, row, k) == "historical"]
    fcst = [(k, c[0]) for k, c in slots if period_role(ctx, row, k) == "forecast"]
    hist_g = _mean_yoy(ctx, row, [c for _k, c in hist])
    fcst_g = _mean_yoy(ctx, row, [c for _k, c in fcst])
    if hist_g is None or fcst_g is None or fcst_g - hist_g < AGGRESSIVE_LIFT:
        return _finish(spec_id, name, [], {}, "assumptions", "low")
    refs = [cell_ref(row, c) for _k, c in hist + fcst]
    return _finish(
        spec_id,
        name,
        refs,
        {"hist_growth": hist_g, "forecast_growth": fcst_g},
        "assumptions",
        "low",
    )


def handle_f04(ctx: FrsCtx, spec_id: str, name: str) -> tuple[ControlResult, FrsIssue | None]:
    ni = book_totals(ctx).get("pnl.net_income")
    if ni is None:
        return _na(spec_id, name), None
    source = _fcf_source(ctx)
    if source == "missing":
        return _insufficient(spec_id, name), None
    slots = year_slots(ctx, ni)
    if not slots:
        return _insufficient(spec_id, name), None
    red: list[tuple[str, list[str]]] = []
    points: list[tuple[str, float | None, float | None, list[str], str]] = []
    for key, cols in slots:
        ni_val = cell_value(ctx, ni, cols[0])
        fcf_val, fcf_refs = _fcf_at(ctx, key)
        cfo_val, cfo_ref = value_at(ctx, "cf.cfo", key)
        ni_ref = cell_ref(ni, cols[0])
        refs = [ni_ref, *fcf_refs]
        if cfo_ref:
            refs.append(cfo_ref)
        points.append((key, cfo_val, fcf_val, refs, ni_ref))
        if (
            period_role(ctx, ni, key) == "forecast"
            and ni_val is not None
            and ni_val >= 0.0
            and fcf_val is not None
            and fcf_val < 0.0
        ):
            red.append((key, refs))
    diverged_refs: list[str] = []
    for prev, cur in zip(points, points[1:], strict=False):
        _pk, cfo0, fcf0, _r0, _n0 = prev
        _ck, cfo1, fcf1, refs, _n1 = cur
        if None in (cfo0, cfo1, fcf0, fcf1):
            continue
        if cfo1 > cfo0 and fcf1 < fcf0:  # type: ignore[operator]
            diverged_refs = refs
            break
    if len(red) < 2 and not diverged_refs:
        return _finish(
            spec_id,
            name,
            [],
            {"fcf_source": source},
            "cash_conversion",
            "medium",
            confidence="medium" if source == "derived" else "high",
        )
    keys = [key for key, _refs in red]
    refs = [ref for _key, item in red for ref in item]
    refs.extend(diverged_refs)
    cause = _f04_cause(ctx, ni, keys)
    metrics = {"period_keys": keys, "fcf_source": source, "cause": cause}
    return _finish(
        spec_id,
        name,
        _uniq(refs),
        metrics,
        "cash_conversion",
        "medium",
        confidence="medium" if source == "derived" else "high",
        cause=cause,
    )


def handle_f06(ctx: FrsCtx, spec_id: str, name: str) -> tuple[ControlResult, FrsIssue | None]:
    totals = book_totals(ctx)
    debt, ebitda = totals.get("bs.debt"), totals.get("pnl.ebitda")
    if debt is None or ebitda is None:
        return _na(spec_id, name), None
    slots = year_slots(ctx, debt, ebitda)
    ratios: list[tuple[str, float, list[str]]] = []
    for key, cols in slots:
        d = cell_value(ctx, debt, cols[0])
        e = cell_value(ctx, ebitda, cols[1])
        if d is None or e is None or e <= 0.0:
            continue
        cash_val, cash_ref = value_at(ctx, "bs.cash", key)
        net = d - cash_val if cash_val is not None else d
        refs = [cell_ref(debt, cols[0]), cell_ref(ebitda, cols[1])]
        if cash_ref:
            refs.append(cash_ref)
        ratios.append((key, net / e, refs))
    if not ratios:
        return _insufficient(spec_id, name), None
    for index, (key, ratio, refs) in enumerate(ratios):
        if ratio >= DEBT_EBITDA_CEILING:
            return _finish(
                spec_id,
                name,
                refs,
                {"ratio": ratio, "period_key": key},
                "leverage",
                "medium",
            )
        if index > 0 and ratio - ratios[index - 1][1] >= DEBT_EBITDA_JUMP:
            return _finish(
                spec_id,
                name,
                refs,
                {
                    "ratio": ratio,
                    "prev_ratio": ratios[index - 1][1],
                    "period_key": key,
                },
                "leverage",
                "medium",
            )
    return _finish(spec_id, name, [], {}, "leverage", "medium")


def handle_f07(ctx: FrsCtx, spec_id: str, name: str) -> tuple[ControlResult, FrsIssue | None]:
    totals = book_totals(ctx)
    ebitda, interest = totals.get("pnl.ebitda"), totals.get("pnl.interest")
    if ebitda is None or interest is None:
        return _na(spec_id, name), None
    slots = year_slots(ctx, ebitda, interest)
    worst: tuple[str, float, list[str]] | None = None
    for key, cols in slots:
        e = cell_value(ctx, ebitda, cols[0])
        i = cell_value(ctx, interest, cols[1])
        if e is None or i is None or i == 0.0:
            continue
        icr = e / abs(i)
        if icr >= ICR_FLOOR:
            continue
        refs = [cell_ref(ebitda, cols[0]), cell_ref(interest, cols[1])]
        if worst is None or icr < worst[1]:
            worst = (key, icr, refs)
    if worst is None:
        return _finish(spec_id, name, [], {"dscr": None}, "leverage", "medium")
    key, icr, refs = worst
    priority = "high" if icr < ICR_HIGH else "medium"
    return _finish(
        spec_id,
        name,
        refs,
        {"icr": icr, "period_key": key, "dscr": None},
        "leverage",
        priority,
    )


def handle_f08(ctx: FrsCtx, spec_id: str, name: str) -> tuple[ControlResult, FrsIssue | None]:
    cash = book_totals(ctx).get("bs.cash")
    if cash is None:
        return _na(spec_id, name), None
    slots = month_slots(ctx, cash)
    if not slots:
        return _insufficient(spec_id, name), None
    values: list[tuple[str, float, str]] = []
    for key, cols in slots:
        val = cell_value(ctx, cash, cols[0])
        if val is None:
            continue
        values.append((key, val, cell_ref(cash, cols[0])))
    if not values:
        return _insufficient(spec_id, name), None
    min_key, min_cash, min_ref = min(values, key=lambda item: item[1])
    runway: int | None = None
    neg_refs: list[str] = []
    for index, (_key, val, ref) in enumerate(values):
        if val < 0.0:
            if runway is None:
                runway = index
            neg_refs.append(ref)
    cash_vals = [val for _k, val, _r in values]
    mean = sum(cash_vals) / len(cash_vals)
    flat = (max(cash_vals) - min(cash_vals)) <= max(1.0, CASH_FLAT * abs(mean))
    draw_refs: list[str] = []
    drew = False
    for key, _val, _ref in values:
        draw, draw_ref = value_at(ctx, "cf.drawdown", key)
        if draw is not None and draw > 0.0:
            drew = True
            if draw_ref:
                draw_refs.append(draw_ref)
    plug = flat and drew
    metrics: dict = {"min_cash": min_cash, "period_key": min_key, "cash_plug": plug}
    if runway is not None:
        metrics["runway"] = runway
    if neg_refs:
        return _finish(
            spec_id,
            name,
            _uniq([*neg_refs, min_ref]),
            metrics,
            "liquidity",
            "high",
        )
    if plug:
        return _finish(
            spec_id,
            name,
            _uniq([item[2] for item in values] + draw_refs),
            metrics,
            "liquidity",
            "medium",
            cause="cash_plug",
        )
    return _finish(spec_id, name, [], metrics, "liquidity", "high")


def handle_f13(ctx: FrsCtx, spec_id: str, name: str) -> tuple[ControlResult, FrsIssue | None]:
    div_row = book_totals(ctx).get("cf.dividends")
    if div_row is None:
        return _na(spec_id, name), None
    source = _fcf_source(ctx)
    if source == "missing":
        return _insufficient(spec_id, name), None
    slots = year_slots(ctx, div_row)
    if not slots:
        return _insufficient(spec_id, name), None
    for key, cols in slots:
        div = cell_value(ctx, div_row, cols[0])
        fcff, fcf_refs = _fcf_at(ctx, key)
        if div is None or fcff is None or div <= 0.0 or fcff >= 0.0:
            continue
        draw, draw_ref = value_at(ctx, "cf.drawdown", key)
        repay, repay_ref = value_at(ctx, "cf.repayment", key)
        metrics: dict = {"fcff": fcff, "period_key": key, "fcf_source": source}
        if draw is not None or repay is not None:
            metrics["fcfe"] = fcff + (draw or 0.0) - (repay or 0.0)
        refs = [cell_ref(div_row, cols[0]), *fcf_refs]
        if draw_ref:
            refs.append(draw_ref)
        if repay_ref:
            refs.append(repay_ref)
        return _finish(
            spec_id,
            name,
            _uniq(refs),
            metrics,
            "dividend_policy",
            "high",
        )
    return _finish(spec_id, name, [], {"fcf_source": source}, "dividend_policy", "high")


def _fcf_source(ctx: FrsCtx) -> str:
    totals = book_totals(ctx)
    if "cf.fcf" in totals:
        return "mapped"
    if "cf.cfo" in totals and "cf.capex" in totals:
        return "derived"
    return "missing"


def _fcf_at(ctx: FrsCtx, key: str) -> tuple[float | None, list[str]]:
    source = _fcf_source(ctx)
    if source == "mapped":
        val, ref = value_at(ctx, "cf.fcf", key)
        return val, [ref] if ref else []
    if source == "derived":
        cfo, cfo_ref = value_at(ctx, "cf.cfo", key)
        capex, capex_ref = value_at(ctx, "cf.capex", key)
        if cfo is None or capex is None:
            return None, []
        return cfo + capex, [ref for ref in (cfo_ref, capex_ref) if ref]
    return None, []


def _f04_cause(ctx: FrsCtx, ni_row: MappedRow, flagged: list[str]) -> str:
    scores = {"ops": 0.0, "wc_ar": 0.0, "wc_ap": 0.0, "capex": 0.0, "dividends": 0.0}
    prev_ar: float | None = None
    prev_ap: float | None = None
    for key, cols in year_slots(ctx, ni_row):
        ni_val = cell_value(ctx, ni_row, cols[0])
        cfo, _ = value_at(ctx, "cf.cfo", key)
        capex, _ = value_at(ctx, "cf.capex", key)
        div, _ = value_at(ctx, "cf.dividends", key)
        ar, _ = value_at(ctx, "bs.ar", key)
        ap, _ = value_at(ctx, "bs.ap", key)
        if key in flagged:
            if ni_val is not None and cfo is not None:
                scores["ops"] += max(0.0, ni_val - cfo)
            if capex is not None:
                scores["capex"] += max(0.0, -capex)
            if div is not None:
                scores["dividends"] += max(0.0, div)
            if ar is not None and prev_ar is not None:
                scores["wc_ar"] += max(0.0, ar - prev_ar)
            if ap is not None and prev_ap is not None:
                scores["wc_ap"] += max(0.0, ap - prev_ap)
        prev_ar, prev_ap = ar, ap
    if all(val == 0.0 for val in scores.values()):
        return "ops"
    return max(scores, key=scores.get)


def _uniq(refs: list[str]) -> list[str]:
    return list(dict.fromkeys(refs))


def _mean_yoy(ctx: FrsCtx, row: MappedRow, cols: list[int]) -> float | None:
    changes: list[float] = []
    for prev, cur in zip(cols, cols[1:], strict=False):
        before = cell_value(ctx, row, prev)
        after = cell_value(ctx, row, cur)
        if before is None or after is None or before == 0.0:
            continue
        changes.append(after / before - 1.0)
    if not changes:
        return None
    return sum(changes) / len(changes)


def _na(spec_id: str, name: str) -> ControlResult:
    return ControlResult(id=spec_id, name=name, status="not_applicable")


def _insufficient(spec_id: str, name: str) -> ControlResult:
    return ControlResult(id=spec_id, name=name, status="insufficient")


def _finish(
    spec_id: str,
    name: str,
    refs: list[str],
    metrics: dict,
    class_name: str,
    priority: str,
    *,
    confidence: str | None = None,
    cause: str = "",
) -> tuple[ControlResult, FrsIssue | None]:
    if not refs:
        return (
            ControlResult(
                id=spec_id,
                name=name,
                status="clear",
                confidence=confidence,  # type: ignore[arg-type]
                metrics=metrics,
            ),
            None,
        )
    issue = FrsIssue(
        id=f"B-{spec_id}",
        control_id=spec_id,
        class_name=class_name,
        priority=priority,  # type: ignore[arg-type]
        metrics=metrics,
        cell_refs=refs,
        cause=cause,
    )
    return (
        ControlResult(
            id=spec_id,
            name=name,
            status="flagged",
            confidence=confidence,  # type: ignore[arg-type]
            cell_refs=refs,
            metrics=metrics,
            issue_id=issue.id,
        ),
        issue,
    )
