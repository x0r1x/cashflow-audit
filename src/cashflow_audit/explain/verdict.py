from __future__ import annotations

from cashflow_audit.explain.models import Finding, Positive, Verdict
from cashflow_audit.frs.models import FrsDocument

_PRIO = {"high": 0, "medium": 1, "low": 2}
_SUBSTANCE = frozenset({"F02", "F06", "F07", "F08"})


def build_verdict(frs: FrsDocument, integrity: list[Finding]) -> Verdict:
    ident = [
        item
        for item in integrity
        if item.detector.startswith("identity.") and item.severity == "error"
    ]
    excel = any(item.detector == "excel_error" for item in integrity)
    high = any(issue.priority == "high" for issue in frs.issues)
    ready: bool | None = False if high or ident or excel else None
    f04 = next((row for row in frs.controls if row.id == "F04"), None)
    f08 = next((row for row in frs.controls if row.id == "F08"), None)
    f09 = next((row for row in frs.controls if row.id == "F09"), None)
    if ident or excel:
        integrity_text = "Расчётная целостность нарушена."
    else:
        integrity_text = "По включённым равенствам явных разрывов нет."
    if f04 is not None and f04.status == "flagged":
        trends = "Прибыль не равна деньгам (NI vs CFO vs FCF)."
    elif any(row.status == "flagged" for row in frs.controls):
        trends = "Есть сигналы по финансовым трендам."
    else:
        trends = (
            "По включённым проверкам F01–F14 существенных трендовых разрывов не видно."
        )
    ranked = sorted(frs.issues, key=lambda issue: _PRIO.get(issue.priority, 9))
    risks = "; ".join(f"{issue.control_id} ({issue.priority})" for issue in ranked)
    if not risks:
        risks = "Ключевых flagged-рисков нет."
    liquidity = (
        f"F08={f08.status if f08 else 'not_applicable'}; "
        f"F09={f09.status if f09 else 'not_applicable'}"
    )
    if ready is False:
        recommendation = (
            "Модель не готова к кредитному процессу, пока не закрыты "
            "перечисленные B-F* и вопросы целостности."
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


def build_positives(frs: FrsDocument) -> list[Positive]:
    found: list[Positive] = []
    for row in frs.controls:
        if row.status != "clear" or row.id not in _SUBSTANCE:
            continue
        found.append(Positive(control_id=row.id, text=row.name))
    return found
