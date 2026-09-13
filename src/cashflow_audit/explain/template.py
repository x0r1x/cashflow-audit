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
    "scenario_switch": "P&L и баланс берут разные сценарии",
    "stress_rate_unchanged": "В стресс-сценарии выручка падает, ставка не меняется",
    "conflicting_rate": "Одна ставка задана в двух местах с разными значениями",
    "agg_range_gap": "Агрегат не покрывает все статьи блока",
    "agg_double_count": "Статья входит в два агрегата СУММ",
    "unused_cell": "Ячейка не влияет на итоговые показатели",
    "hidden_input": "Скрытая ячейка входит в расчёт видимого итога",
    "identity.I1": "Баланс не сходится",
    "identity.I3a": "Касса не сходится с денежным потоком",
    "identity.I3b": "Нераспределённая прибыль не сходится",
    "identity.I4": "Выручка не равна объём × цена",
    "identity.I5": "Валовая прибыль не равна выручка − COGS",
    "identity.I7": "EBIT не равен EBITDA − D&A",
    "identity.I9": "CFO не сходится с прибылью и оборотным капиталом",
    "identity.I10": "График долга не сходится",
    "identity.I11": "Проценты не соответствуют ставке и долгу",
    "identity.I12": "Налог не соответствует ставке и прибыли до налога",
    "identity.I8": "Амортизация P&L не сходится с add-back в ОДДС",
    "identity.I8b": "График основных средств не сходится",
    "frs.F01": "Падение выручки год к году",
    "frs.F02": "Снижение EBITDA или маржи",
    "frs.F03": "Отрицательная EBITDA или убыток несколько периодов",
    "frs.F04": "Прибыль не равна деньгам",
    "frs.F05": "Рост дней оборотного капитала",
    "frs.F06": "Рост долговой нагрузки (net debt / EBITDA)",
    "frs.F07": "Низкое покрытие процентов (EBITDA / проценты)",
    "frs.F08": "Кассовый разрыв или cash plug",
    "frs.F09": "Концентрация погашений",
    "frs.F11": "Прогнозный рост существенно выше истории",
    "frs.F12": "Выручка растёт при падении FCF",
    "frs.F13": "Дивиденды при отрицательном FCFF",
}


def card_title(detector: str) -> str:
    return _TITLES.get(detector, f"Находка {detector}")


def template_card(candidate: Candidate, lineage: LineageItem) -> ExplainCard:
    refs = ", ".join(candidate.cell_refs) or "—"
    metrics = ", ".join(lineage.affected_metrics) or "—"
    title = card_title(candidate.detector)
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
