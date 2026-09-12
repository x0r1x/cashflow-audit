from __future__ import annotations

from cashflow_audit.frs.context import (
    FrsCtx,
    book_totals,
    cell_ref,
    cell_value,
    header_roles,
    month_slots,
    period_role,
    value_at,
    year_amount,
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
DAYS_LIFT = 15.0
AR_OVER_REV = 0.3
REPAY_SHARE = 0.30
REV_LIFT = 0.20
FCF_DROP = -0.20
_WC = ("bs.ar", "bs.inventory", "bs.ap")


def handle_f01(ctx: FrsCtx, spec_id: str, name: str) -> tuple[ControlResult, FrsIssue | None]:
    row = book_totals(ctx).get("pnl.revenue")
    if row is None:
        return _na(spec_id, name), None
    by_key: dict[str, dict[str, int]] = {}
    for key, role, col in header_roles(ctx, row):
        by_key.setdefault(key, {})[role] = col
    for key, roles in by_key.items():
        hist_col, fcst_col = roles.get("historical"), roles.get("forecast")
        if hist_col is None or fcst_col is None:
            continue
        actual = cell_value(ctx, row, hist_col)
        plan = cell_value(ctx, row, fcst_col)
        if actual is None or plan is None or actual == 0.0:
            continue
        change = plan / actual - 1.0
        if abs(change) >= YOY_DROP:
            return _finish(
                spec_id,
                name,
                [cell_ref(row, hist_col), cell_ref(row, fcst_col)],
                {"change": change, "period_key": key},
                "assumptions",
                "low",
            )
    slots = year_slots(ctx, row)
    flagged_refs: list[str] = []
    metrics: dict = {}
    prev_yoy: float | None = None
    for prev, cur in zip(slots, slots[1:], strict=False):
        pkey, _pcols = prev
        ckey, _ccols = cur
        before, before_refs = year_amount(ctx, row, pkey)
        after, after_refs = year_amount(ctx, row, ckey)
        if before is None or after is None or before == 0.0:
            continue
        change = after / before - 1.0
        role = period_role(ctx, row, ckey)
        if role == "forecast" and change <= -YOY_DROP:
            flagged_refs = [*before_refs, *after_refs]
            metrics = {"change": change, "period_key": ckey}
            break
        if (
            period_role(ctx, row, pkey) == "historical"
            and role == "forecast"
            and prev_yoy is not None
            and abs(change - prev_yoy) >= CLIFF_PP
        ):
            flagged_refs = [*before_refs, *after_refs]
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
        pkey, _pcols = prev
        ckey, _ccols = cur
        if period_role(ctx, ebitda, ckey) != "forecast":
            continue
        e0, e0_refs = year_amount(ctx, ebitda, pkey)
        e1, e1_refs = year_amount(ctx, ebitda, ckey)
        r0, _r0_refs = year_amount(ctx, rev, pkey)
        r1, r1_refs = year_amount(ctx, rev, ckey)
        refs = [*e0_refs, *e1_refs, *r1_refs]
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
    run: list[tuple[str, list[str]]] = []
    for key, _cols in slots:
        if period_role(ctx, row, key) != "forecast":
            run = []
            continue
        val, refs = year_amount(ctx, row, key)
        if val is not None and val < 0.0:
            run.append((key, refs))
            if len(run) >= NEG_STREAK:
                return _finish(
                    spec_id,
                    name,
                    [ref for _k, item in run for ref in item],
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
    hist = [k for k, _c in slots if period_role(ctx, row, k) == "historical"]
    fcst = [k for k, _c in slots if period_role(ctx, row, k) == "forecast"]
    hist_g = _mean_yoy_keys(ctx, row, hist)
    fcst_g = _mean_yoy_keys(ctx, row, fcst)
    if hist_g is None or fcst_g is None or fcst_g - hist_g < AGGRESSIVE_LIFT:
        return _finish(spec_id, name, [], {}, "assumptions", "low")
    refs = [ref for key in hist + fcst for ref in year_amount(ctx, row, key)[1]]
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
    for key, _cols in slots:
        ni_val, ni_refs = year_amount(ctx, ni, key)
        fcf_val, fcf_refs = _fcf_at(ctx, key)
        cfo_val, cfo_ref = value_at(ctx, "cf.cfo", key)
        refs = [*ni_refs, *fcf_refs]
        if cfo_ref:
            refs.append(cfo_ref)
        points.append((key, cfo_val, fcf_val, refs, ni_refs[0] if ni_refs else ""))
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
    snap_key = keys[0] if keys else (points[1][0] if len(points) > 1 else None)
    metrics: dict = {"period_keys": keys, "fcf_source": source, "cause": cause}
    if snap_key is not None:
        ni_snap, _ = year_amount(ctx, ni, snap_key)
        cfo_snap, _ = value_at(ctx, "cf.cfo", snap_key)
        fcf_snap, _ = _fcf_at(ctx, snap_key)
        metrics["ni"] = ni_snap
        metrics["cfo"] = cfo_snap
        metrics["fcf"] = fcf_snap
        if ni_snap is not None and cfo_snap is not None:
            metrics["accruals"] = ni_snap - cfo_snap
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
    for key, _cols in slots:
        d, d_refs = year_amount(ctx, debt, key)
        e, e_refs = year_amount(ctx, ebitda, key)
        if d is None or e is None or e <= 0.0:
            continue
        cash_val, cash_ref = value_at(ctx, "bs.cash", key)
        net = d - cash_val if cash_val is not None else d
        refs = [*d_refs, *e_refs]
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
    for key, _cols in slots:
        e, e_refs = year_amount(ctx, ebitda, key)
        i, i_refs = year_amount(ctx, interest, key)
        if e is None or i is None or i == 0.0:
            continue
        icr = e / abs(i)
        if icr >= ICR_FLOOR:
            continue
        refs = [*e_refs, *i_refs]
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
    for key, _cols in slots:
        div, div_refs = year_amount(ctx, div_row, key)
        fcff, fcf_refs = _fcf_at(ctx, key)
        if div is None or fcff is None or div <= 0.0 or fcff >= 0.0:
            continue
        draw, draw_ref = value_at(ctx, "cf.drawdown", key)
        repay, repay_ref = value_at(ctx, "cf.repayment", key)
        metrics: dict = {"fcff": fcff, "period_key": key, "fcf_source": source}
        if draw is not None or repay is not None:
            metrics["fcfe"] = fcff + (draw or 0.0) - (repay or 0.0)
        refs = [*div_refs, *fcf_refs]
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


def handle_f05(ctx: FrsCtx, spec_id: str, name: str) -> tuple[ControlResult, FrsIssue | None]:
    totals = book_totals(ctx)
    if sum(1 for cid in _WC if cid in totals) < 2:
        return _na(spec_id, name), None
    ar, rev = totals.get("bs.ar"), totals.get("pnl.revenue")
    if ar is None or rev is None:
        return _na(spec_id, name), None
    hist_dso, fcst_dso = _days_series(ctx, "bs.ar", "pnl.revenue")
    hist_dpo, fcst_dpo = _days_series(ctx, "bs.ap", "pnl.cogs")
    hist_dio, fcst_dio = _days_series(ctx, "bs.inventory", "pnl.cogs")
    metrics: dict = {}
    refs: list[str] = []
    if fcst_dso:
        metrics["dso"] = fcst_dso[-1][1]
        if hist_dso is not None:
            for _key, days, item_refs in fcst_dso:
                if days - hist_dso >= DAYS_LIFT:
                    metrics["dso"] = days
                    refs.extend(item_refs)
                    break
    if totals.get("pnl.cogs") is None:
        metrics["dio"] = None
        metrics["dpo"] = None
    else:
        if fcst_dio:
            metrics["dio"] = fcst_dio[-1][1]
        if fcst_dpo:
            metrics["dpo"] = fcst_dpo[-1][1]
            if hist_dpo is not None:
                for _key, days, item_refs in fcst_dpo:
                    if days - hist_dpo >= DAYS_LIFT:
                        metrics["dpo"] = days
                        refs.extend(item_refs)
                        break
    prev_ar: float | None = None
    prev_rev: float | None = None
    for _key, _cols in year_slots(ctx, ar, rev):
        a, _a_refs = year_amount(ctx, ar, _key)
        r, _r_refs = year_amount(ctx, rev, _key)
        if (
            prev_ar is not None
            and prev_rev is not None
            and a is not None
            and r is not None
            and r > prev_rev
            and (a - prev_ar) > AR_OVER_REV * (r - prev_rev)
        ):
            refs.extend([*_a_refs, *_r_refs])
        if a is not None:
            prev_ar = a
        if r is not None:
            prev_rev = r
    return _finish(spec_id, name, _uniq(refs), metrics, "cash_conversion", "medium")


def handle_f09(ctx: FrsCtx, spec_id: str, name: str) -> tuple[ControlResult, FrsIssue | None]:
    row = book_totals(ctx).get("cf.repayment")
    if row is None:
        return _na(spec_id, name), None
    slots = month_slots(ctx, row)
    if not slots:
        return _insufficient(spec_id, name), None
    by_year: dict[str, float] = {}
    refs_by_year: dict[str, list[str]] = {}
    peak: dict[str, tuple[str, float]] = {}
    total = 0.0
    for key, cols in slots:
        if period_role(ctx, row, key) != "forecast":
            continue
        val = cell_value(ctx, row, cols[0])
        if val is None or val <= 0.0:
            continue
        year = key[:4]
        by_year[year] = by_year.get(year, 0.0) + val
        total += val
        ref = cell_ref(row, cols[0])
        refs_by_year.setdefault(year, []).append(ref)
        if year not in peak or val > peak[year][1]:
            peak[year] = (key, val)
    if total <= 0.0 or not by_year:
        return _insufficient(spec_id, name), None
    year = max(by_year, key=by_year.get)
    share = by_year[year] / total
    metrics = {
        "share": share,
        "year": year,
        "peak_month": peak[year][0],
    }
    if share >= REPAY_SHARE:
        return _finish(
            spec_id,
            name,
            refs_by_year[year],
            metrics,
            "refinancing",
            "medium",
        )
    return _finish(spec_id, name, [], metrics, "refinancing", "medium")


def handle_f10(ctx: FrsCtx, spec_id: str, name: str) -> tuple[ControlResult, FrsIssue | None]:
    if book_totals(ctx).get("pnl.interest") is None:
        return _na(spec_id, name), None
    return (
        _insufficient(spec_id, name, metrics={"fx": None}, evidence="FX не рассчитан"),
        None,
    )


def handle_f12(ctx: FrsCtx, spec_id: str, name: str) -> tuple[ControlResult, FrsIssue | None]:
    rev = book_totals(ctx).get("pnl.revenue")
    if rev is None:
        return _na(spec_id, name), None
    source = _fcf_source(ctx)
    if source == "missing":
        return _insufficient(spec_id, name), None
    fcst = [
        (key, cols)
        for key, cols in year_slots(ctx, rev)
        if period_role(ctx, rev, key) == "forecast"
    ]
    if len(fcst) < 2:
        return _insufficient(spec_id, name), None
    first_key, _first_cols = fcst[0]
    last_key, _last_cols = fcst[-1]
    r0, r0_refs = year_amount(ctx, rev, first_key)
    r1, r1_refs = year_amount(ctx, rev, last_key)
    f0, f0_refs = _fcf_at(ctx, first_key)
    f1, f1_refs = _fcf_at(ctx, last_key)
    if r0 is None or r1 is None or r0 == 0.0 or f0 is None or f1 is None:
        return _insufficient(spec_id, name), None
    rev_chg = r1 / r0 - 1.0
    fcf_chg = None if f0 == 0.0 else f1 / f0 - 1.0
    tail = [_fcf_at(ctx, key)[0] for key, _cols in fcst[-2:]]
    gone_neg = f0 >= 0.0 and len(tail) >= 2 and all(val is not None and val < 0.0 for val in tail)
    fcf_bad = (fcf_chg is not None and fcf_chg <= FCF_DROP) or gone_neg
    capex0, capex0_ref = value_at(ctx, "cf.capex", first_key)
    capex1, capex1_ref = value_at(ctx, "cf.capex", last_key)
    metrics = {
        "revenue_change": rev_chg,
        "fcf_change": fcf_chg,
        "fcf_source": source,
    }
    if rev_chg < REV_LIFT or not fcf_bad:
        return _finish(spec_id, name, [], metrics, "economic_logic", "medium")
    refs = [
        *r0_refs,
        *r1_refs,
        *f0_refs,
        *f1_refs,
    ]
    if capex0_ref:
        refs.append(capex0_ref)
    if capex1_ref:
        refs.append(capex1_ref)
    if capex0 is not None:
        metrics["capex_start"] = capex0
    if capex1 is not None:
        metrics["capex_end"] = capex1
    return _finish(spec_id, name, _uniq(refs), metrics, "economic_logic", "medium")


def handle_f14(ctx: FrsCtx, spec_id: str, name: str) -> tuple[ControlResult, FrsIssue | None]:
    cash = book_totals(ctx).get("bs.cash")
    if cash is None:
        return _na(spec_id, name), None
    slots = month_slots(ctx, cash) or year_slots(ctx, cash)
    cash_vals = [
        val
        for key, cols in slots
        if (val := cell_value(ctx, cash, cols[0])) is not None
    ]
    metrics: dict = {}
    if cash_vals:
        metrics["min_cash"] = min(cash_vals)
    return (
        _insufficient(spec_id, name, metrics=metrics, evidence="ковенанты не заданы"),
        None,
    )


def _days_series(
    ctx: FrsCtx, stock_id: str, flow_id: str
) -> tuple[float | None, list[tuple[str, float, list[str]]]]:
    totals = book_totals(ctx)
    stock, flow = totals.get(stock_id), totals.get(flow_id)
    if stock is None or flow is None:
        return None, []
    hist: list[float] = []
    fcst: list[tuple[str, float, list[str]]] = []
    for key, _cols in year_slots(ctx, stock, flow):
        s, s_refs = year_amount(ctx, stock, key)
        f, f_refs = year_amount(ctx, flow, key)
        if s is None or f is None or f == 0.0:
            continue
        days = s * 365.0 / f
        refs = [*s_refs, *f_refs]
        role = period_role(ctx, stock, key)
        if role == "historical":
            hist.append(days)
        elif role == "forecast":
            fcst.append((key, days, refs))
    return (hist[-1] if hist else None), fcst


def _fcf_source(ctx: FrsCtx) -> str:
    totals = book_totals(ctx)
    if "cf.fcf" in totals:
        return "mapped"
    if "cf.cfo" in totals and "cf.capex" in totals:
        return "derived"
    return "missing"


def _fcf_at(ctx: FrsCtx, key: str) -> tuple[float | None, list[str]]:
    totals = book_totals(ctx)
    source = _fcf_source(ctx)
    if source == "mapped":
        return year_amount(ctx, totals["cf.fcf"], key)
    if source == "derived":
        cfo, cfo_refs = year_amount(ctx, totals["cf.cfo"], key)
        capex, capex_refs = year_amount(ctx, totals["cf.capex"], key)
        if cfo is None or capex is None:
            return None, []
        return cfo + capex, [*cfo_refs, *capex_refs]
    return None, []


def _f04_cause(ctx: FrsCtx, ni_row: MappedRow, flagged: list[str]) -> str:
    scores = {"ops": 0.0, "wc_ar": 0.0, "wc_ap": 0.0, "capex": 0.0, "dividends": 0.0}
    prev_ar: float | None = None
    prev_ap: float | None = None
    for key, _cols in year_slots(ctx, ni_row):
        ni_val, _ = year_amount(ctx, ni_row, key)
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


def _mean_yoy_keys(ctx: FrsCtx, row: MappedRow, keys: list[str]) -> float | None:
    changes: list[float] = []
    for prev, cur in zip(keys, keys[1:], strict=False):
        before, _ = year_amount(ctx, row, prev)
        after, _ = year_amount(ctx, row, cur)
        if before is None or after is None or before == 0.0:
            continue
        changes.append(after / before - 1.0)
    if not changes:
        return None
    return sum(changes) / len(changes)


def _na(spec_id: str, name: str) -> ControlResult:
    return ControlResult(id=spec_id, name=name, status="not_applicable")


def _insufficient(
    spec_id: str,
    name: str,
    metrics: dict | None = None,
    evidence: str = "",
) -> ControlResult:
    return ControlResult(
        id=spec_id,
        name=name,
        status="insufficient",
        metrics=metrics or {},
        evidence=evidence,
    )


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
