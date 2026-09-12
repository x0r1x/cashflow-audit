from __future__ import annotations

from cashflow_audit.frs.context import (
    FrsCtx,
    book_totals,
    cell_ref,
    cell_value,
    period_role,
    year_slots,
)
from cashflow_audit.frs.models import ControlResult, FrsIssue
from cashflow_audit.mapping.models import MappedRow

YOY_DROP = 0.10
MARGIN_DROP = 0.05
NEG_STREAK = 2
AGGRESSIVE_LIFT = 0.15
CLIFF_PP = 0.15


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


def _finish(
    spec_id: str,
    name: str,
    refs: list[str],
    metrics: dict,
    class_name: str,
    priority: str,
) -> tuple[ControlResult, FrsIssue | None]:
    if not refs:
        return ControlResult(id=spec_id, name=name, status="clear"), None
    issue = FrsIssue(
        id=f"B-{spec_id}",
        control_id=spec_id,
        class_name=class_name,
        priority=priority,  # type: ignore[arg-type]
        metrics=metrics,
        cell_refs=refs,
    )
    return (
        ControlResult(
            id=spec_id,
            name=name,
            status="flagged",
            cell_refs=refs,
            metrics=metrics,
            issue_id=issue.id,
        ),
        issue,
    )
