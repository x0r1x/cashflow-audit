from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cashflow_audit.explain.models import ControlExplanation, RiskScreenRow
from cashflow_audit.frs.catalog import CATALOG
from cashflow_audit.frs.models import ControlResult, FrsDocument, FrsIssue
from cashflow_audit.mapping.models import MappingDocument


@dataclass(frozen=True)
class _Narrative:
    check: str
    impact: str
    next_step: str


_NARRATIVES: dict[str, _Narrative] = {
    "F01": _Narrative(
        "Сопоставляет динамику выручки по годам и переход от факта к прогнозу.",
        "Падение или резкий перелом выручки может означать сокращение бизнеса "
        "или завышенный переход к прогнозу.",
        "Сверить объём, цену, валютный эффект и разовые факторы указанного периода.",
    ),
    "F02": _Narrative(
        "Проверяет динамику EBITDA и EBITDA-маржи в прогнозе.",
        "Снижение EBITDA или маржи уменьшает запас для обслуживания долга.",
        "Сверить изменение выручки, COGS и OPEX в отмеченном периоде.",
    ),
    "F03": _Narrative(
        "Ищет повторяющиеся прогнозные периоды с убытком или отрицательной EBITDA.",
        "Серия убытков указывает на отсутствие устойчивого восстановления.",
        "Проверить меры восстановления прибыльности и источники финансирования убытков.",
    ),
    "F04": _Narrative(
        "Сопоставляет чистую прибыль, CFO и FCF и ищет длительный разрыв прибыль→деньги.",
        "Слабая конверсия прибыли в деньги повышает риск дефицита ликвидности.",
        "Разобрать мост NI→CFO→FCF: операции, оборотный капитал, CAPEX и дивиденды.",
    ),
    "F05": _Narrative(
        "Проверяет DSO, DIO и DPO и рост дебиторской задолженности относительно выручки.",
        "Ухудшение оборачиваемости замораживает деньги в оборотном капитале.",
        "Сверить сроки оплаты, запасы, кредиторскую задолженность и базу COGS.",
    ),
    "F06": _Narrative(
        "Проверяет уровень и изменение Net Debt/EBITDA.",
        "Рост долговой нагрузки снижает запас по ковенантам и кредитному качеству.",
        "Сверить долг, кассу, EBITDA и план погашения в отмеченном периоде.",
    ),
    "F07": _Narrative(
        "Проверяет ICR и только явно mapped DSCR, LLCR и PLCR.",
        "Низкое покрытие означает небольшой запас на обслуживание долга.",
        "Сверить EBITDA, проценты и строки ковенантных коэффициентов в модели.",
    ),
    "F08": _Narrative(
        "Проверяет минимальную кассу, момент ухода в минус и возможный cash plug.",
        "Отрицательная касса или искусственное удержание остатка означает потребность "
        "в финансировании.",
        "Проверить помесячный остаток кассы, выборки долга и доступные лимиты.",
    ),
    "F09": _Narrative(
        "Измеряет долю прогнозных погашений, сосредоточенную в одном году.",
        "Крупный пик погашений создаёт риск рефинансирования.",
        "Сверить график погашений и подтверждённые источники рефинансирования.",
    ),
    "F10": _Narrative(
        "Проверяет, есть ли в модели mapped валютный курс рядом с процентными расходами.",
        "Без курса валютный эффект и устойчивость процентной нагрузки оценить нельзя.",
        "Проверить строку fx.rate и валютную согласованность долга, выручки и процентов.",
    ),
    "F11": _Narrative(
        "Сопоставляет средний рост выручки в прогнозе с историческим ростом.",
        "Слишком сильное улучшение прогноза повышает риск завышенных предпосылок.",
        "Сверить прогноз роста с историей и подтверждающими коммерческими драйверами.",
    ),
    "F12": _Narrative(
        "Сопоставляет рост выручки с динамикой FCF и CAPEX.",
        "Рост бизнеса без денежной конверсии может быть экономически несогласован.",
        "Сверить FCF, инвестиционную программу и оборотный капитал с ростом выручки.",
    ),
    "F13": _Narrative(
        "Сопоставляет дивиденды с FCFF/FCFE и чистым привлечением долга.",
        "Дивиденды при отрицательном свободном потоке могут финансироваться новым долгом.",
        "Сверить дивидендную политику, FCF, выборки и погашения долга.",
    ),
    "F14": _Narrative(
        "Проверяет явно mapped запас по ковенанту и минимальную кассу.",
        "Нулевой или отрицательный headroom означает отсутствие запаса прочности.",
        "Сверить covenant.headroom с условиями договора и стресс-сценарием.",
    ),
}

_PERCENT_KEYS = {
    "change",
    "prev_yoy",
    "margin",
    "margin_drop",
    "hist_growth",
    "forecast_growth",
    "revenue_change",
    "fcf_change",
    "volume_change",
    "price_change",
    "fx_change",
    "share",
}
_RATIO_KEYS = {"ratio", "prev_ratio", "icr", "dscr", "llcr", "plcr", "nd_ebitda"}
_LABELS = {
    "change": "изменение",
    "prev_yoy": "предыдущий темп",
    "margin": "маржа EBITDA",
    "margin_drop": "изменение маржи",
    "hist_growth": "рост в истории",
    "forecast_growth": "рост в прогнозе",
    "revenue_change": "изменение выручки",
    "fcf_change": "изменение FCF",
    "volume_change": "изменение объёма",
    "price_change": "изменение цены",
    "fx_change": "изменение FX",
    "ratio": "ND/EBITDA",
    "prev_ratio": "предыдущий ND/EBITDA",
    "icr": "ICR",
    "dscr": "DSCR",
    "llcr": "LLCR",
    "plcr": "PLCR",
    "nd_ebitda": "макс. ND/EBITDA",
    "ni": "NI",
    "cfo": "CFO",
    "fcf": "FCF",
    "fcff": "FCFF",
    "fcfe": "FCFE",
    "min_cash": "минимальная касса",
    "runway": "runway, периодов",
    "dso": "DSO, дней",
    "dio": "DIO, дней",
    "dpo": "DPO, дней",
    "min_headroom": "минимальный headroom",
    "period_key": "период",
    "period_keys": "периоды",
    "cols": "периоды",
    "year": "год",
    "peak_month": "пиковый месяц",
    "fcf_source": "источник FCF",
    "cash_plug": "cash plug",
    "fx": "FX",
}
_ORDER = {
    "F01": ("change", "prev_yoy", "volume_change", "price_change", "fx_change", "period_key"),
    "F02": ("change", "margin_drop", "margin", "period_key"),
    "F03": ("cols",),
    "F04": ("ni", "cfo", "fcf", "fcf_source", "period_keys"),
    "F05": ("dso", "dio", "dpo"),
    "F06": ("prev_ratio", "ratio", "period_key"),
    "F07": ("icr", "dscr", "llcr", "plcr", "period_key"),
    "F08": ("min_cash", "runway", "cash_plug", "period_key"),
    "F09": ("share", "year", "peak_month"),
    "F10": ("fx",),
    "F11": ("hist_growth", "forecast_growth"),
    "F12": ("revenue_change", "fcf_change", "fcf_source"),
    "F13": ("fcff", "fcfe", "fcf_source"),
    "F14": ("min_headroom", "min_cash", "nd_ebitda", "icr", "period_key"),
}
_CAUSES = {
    "ops": "операционная прибыль не конвертируется в денежный поток",
    "wc_ar": "рост дебиторской задолженности удерживает деньги в обороте",
    "wc_ap": "динамика кредиторской задолженности ухудшает денежную конверсию",
    "capex": "инвестиционные выплаты снижают свободный денежный поток",
    "dividends": "дивидендные выплаты снижают доступный денежный поток",
    "cash_plug": "остаток кассы поддерживается выборкой долга и похож на cash plug",
}


def build_risk_screen(
    frs: FrsDocument,
    mapping: MappingDocument,
) -> list[RiskScreenRow]:
    mapped = {row.concept_id for row in mapping.rows if row.concept_id}
    specs = {spec.id: spec for spec in CATALOG}
    rows: list[RiskScreenRow] = []
    for control in frs.controls:
        narrative = _NARRATIVES.get(
            control.id,
            _Narrative(
                check=control.name,
                impact="Влияние определяется результатом контроля.",
                next_step="Проверить исходные строки и допущения контроля.",
            ),
        )
        spec = specs.get(control.id)
        missing = sorted(spec.need - mapped) if spec is not None else []
        rows.append(
            RiskScreenRow(
                **control.model_dump(),
                explanation=ControlExplanation(
                    check=narrative.check,
                    status_reason=_status_reason(control, missing),
                    key_fact=_key_fact(control),
                    impact=_impact(control, narrative),
                    next_step=narrative.next_step,
                    cited_refs=list(control.cell_refs),
                ),
            )
        )
    return rows


def enrich_issues(
    issues: list[FrsIssue],
    rows: list[RiskScreenRow],
) -> list[FrsIssue]:
    by_id = {row.id: row for row in rows}
    enriched: list[FrsIssue] = []
    for issue in issues:
        row = by_id.get(issue.control_id)
        cause = _CAUSES.get(issue.cause, issue.cause.strip())
        impact = issue.impact.strip()
        if row is not None:
            if not cause:
                cause = (
                    f"{row.explanation.status_reason} "
                    f"{row.explanation.key_fact}"
                ).strip()
            if not impact:
                impact = row.explanation.impact
        enriched.append(issue.model_copy(update={"cause": cause, "impact": impact}))
    return enriched


def _status_reason(control: ControlResult, missing: list[str]) -> str:
    if control.status == "flagged":
        issue = f" ({control.issue_id})" if control.issue_id else ""
        return f"Порог контроля нарушен{issue}; связанные ячейки указаны в cell_refs."
    if control.status == "clear":
        return "По доступным данным порог контроля не нарушен."
    if control.status == "insufficient":
        detail = control.evidence.strip() or "не хватает периода или дополнительного драйвера"
        return f"Проверка применима, но вывод ограничен: {detail}."
    if missing:
        return "Проверка не выполнена: в mapping нет " + ", ".join(missing) + "."
    return "Проверка не выполнена: обязательная статья не распознана."


def _key_fact(control: ControlResult) -> str:
    pieces: list[str] = []
    seen: set[str] = set()
    for key in _ORDER.get(control.id, ()):
        if key not in control.metrics:
            continue
        rendered = _metric(key, control.metrics[key])
        if rendered:
            pieces.append(rendered)
            seen.add(key)
    for key, value in control.metrics.items():
        if key in seen or key == "cause":
            continue
        rendered = _metric(key, value)
        if rendered:
            pieces.append(rendered)
    if pieces:
        return "; ".join(pieces) + "."
    if control.status == "clear":
        return "Пороговый сигнал по рассчитанным данным не зафиксирован."
    if control.status == "flagged":
        return "Сигнал подтверждён указанными cell_refs; отдельная числовая метрика не рассчитана."
    return "Числовой вывод не рассчитан из-за отсутствующих данных."


def _impact(control: ControlResult, narrative: _Narrative) -> str:
    if control.status == "clear":
        return f"Этот риск по доступным данным не выявлен. {narrative.impact}"
    if control.status in {"insufficient", "not_applicable"}:
        return f"Сделать вывод по этому риску нельзя. {narrative.impact}"
    return narrative.impact


def _metric(key: str, value: Any) -> str:
    if value is None:
        return ""
    label = _LABELS.get(key, key.replace("_", " "))
    if key in _PERCENT_KEYS and _is_number(value):
        return f"{label} {_percent(float(value))}"
    if key in _RATIO_KEYS and _is_number(value):
        return f"{label} {_number(float(value))}x"
    if isinstance(value, bool):
        return f"{label}: {'да' if value else 'нет'}"
    if isinstance(value, list):
        if not value:
            return ""
        return f"{label}: {', '.join(str(item) for item in value)}"
    if _is_number(value):
        return f"{label} {_number(float(value))}"
    if value == "mapped":
        return f"{label}: строка распознана"
    return f"{label}: {value}"


def _percent(value: float) -> str:
    return f"{_number(value * 100)}%"


def _number(value: float) -> str:
    if value == 0:
        return "0"
    sign = "−" if value < 0 else ""
    absolute = abs(value)
    if absolute.is_integer():
        text = f"{int(absolute):,}".replace(",", " ")
    else:
        text = f"{absolute:,.2f}".replace(",", " ").rstrip("0").rstrip(".")
    return sign + text


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
