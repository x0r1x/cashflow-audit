from __future__ import annotations

from cashflow_audit.checkers.models import Candidate
from cashflow_audit.explain.models import ExplainCard
from cashflow_audit.lineage.models import LineageItem

_TITLES = {
    "excel_error": "Ячейка содержит ошибку Excel",
    "error_masking": "IFERROR скрывает ошибку Excel",
    "circular": "Циклическая ссылка",
    "unresolved_dynamic": "Динамическая ссылка не разрешена",
    "external_link": "Ссылка на внешний файл",
    "xlm_or_vba": "Книга содержит VBA или XLM",
    "pattern_break": "Разрыв ряда формул",
    "value_instead_of_formula": "Формула заменена значением",
    "literal_in_ast": "В формуле зашита константа",
    "source_switch": "Смена листа-источника в ряду",
    "agg_range_gap": "Агрегат не покрывает все статьи блока",
    "unused_cell": "Ячейка не влияет на итоговые показатели",
    "hidden_input": "Скрытая ячейка входит в расчёт видимого итога",
    "identity.I1": "Баланс не сходится",
    "identity.I3a": "Касса не сходится с денежным потоком",
    "identity.I3b": "Нераспределённая прибыль не сходится",
}


def template_card(candidate: Candidate, lineage: LineageItem) -> ExplainCard:
    refs = ", ".join(candidate.cell_refs) or "—"
    metrics = ", ".join(lineage.affected_metrics) or "—"
    title = _TITLES.get(candidate.detector, f"Находка {candidate.detector}")
    evidence = f"Детектор {candidate.detector}; ячейки {refs}; метрики {metrics}."
    recommendation = "Проверить указанные ячейки. Файл не изменён."
    return ExplainCard(title=title, evidence=evidence, recommendation=recommendation)


def impact_text(lineage: LineageItem) -> str:
    if lineage.impact.kind == "numeric":
        value = lineage.impact.value
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return f"≈{value} ед. отчётности"
    return f"направление: {lineage.impact.value}"
