from __future__ import annotations

from typing import Any

from cashflow_audit.explain.models import Finding, Positive, RiskScreenRow, Verdict
from cashflow_audit.frs.models import ControlResult, FrsDocument, FrsIssue

_PRIO = {"high": 0, "medium": 1, "low": 2}


def _positive_f02(row: ControlResult) -> str | None:
    margin = row.metrics.get("margin")
    if isinstance(margin, (int, float)):
        return f"Маржа EBITDA {_fmt(margin)}"
    if row.metrics:
        return None
    return "EBITDA-маржа без существенного снижения"


def _positive_f06(row: ControlResult) -> str | None:
    ratio = row.metrics.get("ratio")
    prev = row.metrics.get("prev_ratio")
    if isinstance(ratio, (int, float)) and isinstance(prev, (int, float)) and ratio < prev:
        return f"ND/EBITDA снизился с {_fmt(prev)}x до {_fmt(ratio)}x"
    if isinstance(ratio, (int, float)):
        return f"ND/EBITDA {_fmt(ratio)}x, без роста сверх порога"
    return "ND/EBITDA не растёт сверх порога"


def _positive_f07(row: ControlResult) -> str | None:
    icr = row.metrics.get("icr")
    if isinstance(icr, (int, float)):
        return f"ICR {_fmt(icr)}x"
    return "ICR выше порога"


def _positive_f08(row: ControlResult) -> str | None:
    min_cash = row.metrics.get("min_cash")
    period = row.metrics.get("period_key")
    if isinstance(min_cash, (int, float)):
        suffix = f" ({period})" if period else ""
        return f"min cash {_fmt(min_cash)}{suffix}"
    return "Касса не уходит в минус"


# Bind after defs — dict above referenced functions before assignment.
_SUBSTANCE = {
    "F02": _positive_f02,
    "F06": _positive_f06,
    "F07": _positive_f07,
    "F08": _positive_f08,
}


def build_verdict(
    frs: FrsDocument,
    integrity: list[Finding],
    *,
    risk_screen: list[RiskScreenRow] | None = None,
    issues: list[FrsIssue] | None = None,
) -> Verdict:
    rows = risk_screen or list(frs.controls)
    issue_rows = issues or frs.issues
    ident = [
        item
        for item in integrity
        if item.detector.startswith("identity.") and item.severity == "error"
    ]
    excel = any(item.detector == "excel_error" for item in integrity)
    high = any(issue.priority == "high" for issue in issue_rows)
    ready: bool | None = False if high or ident or excel else None
    f04 = next((row for row in rows if row.id == "F04"), None)
    f08 = next((row for row in rows if row.id == "F08"), None)
    f09 = next((row for row in rows if row.id == "F09"), None)
    if ident or excel:
        integrity_text = "Расчётная целостность нарушена."
    else:
        integrity_text = "По включённым равенствам явных разрывов нет."
    if f04 is not None and f04.status == "flagged":
        trends = "Прибыль не равна деньгам (NI vs CFO vs FCF)."
    elif flagged := [row for row in rows if row.status == "flagged"]:
        trends = "Есть сигналы: " + "; ".join(
            f"{row.id} {row.name}" for row in flagged
        ) + "."
    else:
        trends = (
            "По включённым проверкам F01–F14 существенных трендовых разрывов не видно."
        )
    ranked = sorted(issue_rows, key=lambda issue: _PRIO.get(issue.priority, 9))
    names = {row.id: row.name for row in rows}
    risks = "; ".join(
        f"{issue.control_id} {names.get(issue.control_id, '')} ({issue.priority})".strip()
        for issue in ranked
    )
    if not risks:
        risks = "Ключевых flagged-рисков нет."
    incomplete = [
        row for row in rows if row.status in {"insufficient", "not_applicable"}
    ]
    if incomplete:
        risks += " Не покрыты полностью: " + ", ".join(
            f"{row.id} ({row.status})" for row in incomplete
        ) + "."
    liquidity = _liquidity_text(f08, f09)
    if ready is False:
        blockers = [issue.id for issue in ranked if issue.priority == "high"]
        blocker_text = ", ".join(blockers) or "вопросы целостности"
        recommendation = (
            "Модель не готова к кредитному процессу: сначала проверить "
            f"{blocker_text} и ошибки расчётной целостности."
        )
    elif incomplete:
        recommendation = (
            "Сначала дополнить mapping/исходные данные для "
            + ", ".join(row.id for row in incomplete)
            + "; по остальным включённым проверкам существенных рисков не видно."
        )
    else:
        recommendation = (
            "По включённым проверкам F01–F14 существенных рисков не видно; "
            "полнота не гарантируется."
        )
    return Verdict(
        integrity=integrity_text,
        trends=trends,
        risks=risks,
        liquidity=liquidity,
        recommendation=recommendation,
        ready_for_credit=ready,
    )


def build_positives(
    frs: FrsDocument,
    risk_screen: list[RiskScreenRow] | None = None,
) -> list[Positive]:
    explanations = {row.id: row.explanation for row in risk_screen or []}
    found: list[Positive] = []
    for row in frs.controls:
        if row.status != "clear":
            continue
        builder = _SUBSTANCE.get(row.id)
        text = builder(row) if builder is not None else None
        explanation = explanations.get(row.id)
        if text is None and row.metrics and explanation is not None:
            text = f"{row.name}: {explanation.key_fact.rstrip('.')}"
        if not text:
            continue
        found.append(Positive(control_id=row.id, text=text))
    return found


def _liquidity_text(f08: ControlResult | None, f09: ControlResult | None) -> str:
    parts: list[str] = []
    if f08 is not None:
        runway = f08.metrics.get("runway")
        min_cash = f08.metrics.get("min_cash")
        if isinstance(runway, (int, float)):
            parts.append(f"F08 runway {_fmt(runway)} периодов до отрицательной кассы")
        elif "не иссякает" in (f08.evidence or ""):
            parts.append("F08 касса не иссякает")
        elif isinstance(min_cash, (int, float)):
            parts.append(f"F08 min cash {_fmt(min_cash)} ({f08.status})")
        else:
            parts.append(f"F08={f08.status}")
    else:
        parts.append("F08=not_applicable")
    if f09 is not None:
        share = f09.metrics.get("share")
        year = f09.metrics.get("year")
        if isinstance(share, (int, float)):
            pct = _fmt(share * 100)
            year_bit = f" в {year}" if year else ""
            parts.append(f"F09 концентрация погашений {pct}%{year_bit}")
        else:
            parts.append(f"F09={f09.status}")
    else:
        parts.append("F09=not_applicable")
    return "; ".join(parts)


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        text = f"{value:.4f}".rstrip("0").rstrip(".")
        return text
    return str(value)
