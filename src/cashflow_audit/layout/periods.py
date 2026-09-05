from __future__ import annotations

import re

from cashflow_audit.layout.models import ColumnRole, PeriodHit

_YEAR = re.compile(r"^(?:FY\s*)?((?:19|20)\d{2})([EFAPеЕфФпПaA])?$", re.IGNORECASE)
_YEAR_FLOAT = re.compile(r"^((?:19|20)\d{2})(?:\.0+)?$")
_QUARTER_RU = re.compile(
    r"^([1-4])\s*кв\.?\s*((?:19|20)\d{2})([EFAPеЕ])?$",
    re.IGNORECASE,
)
_QUARTER_EN = re.compile(r"^Q([1-4])\s*((?:19|20)\d{2})([EFAP])?$", re.IGNORECASE)
_TOTAL = re.compile(r"^(итого|всего|total|sum)$", re.IGNORECASE)
_STUB = re.compile(r"^(stub|частичн\w*|partial)$", re.IGNORECASE)
_SCENARIO = re.compile(
    r"^(base(?:\s*case)?|upside|downside|негативн\w*|позитивн\w*|stress|сценарий(?:\s+\w+)?)$",
    re.IGNORECASE,
)
_FACT = re.compile(r"^(факт\w*|actual|hist(?:orical)?)$", re.IGNORECASE)
_PLAN = re.compile(r"^(план\w*|прогноз\w*|forecast|budget)$", re.IGNORECASE)
_FORECAST_SFX = {"E", "F", "P", "Е", "П"}


def classify_header(text: str | None) -> PeriodHit | None:
    if text is None:
        return None
    raw = re.sub(r"\s+", " ", str(text).replace("\xa0", " ")).strip()
    if not raw:
        return None
    if _TOTAL.fullmatch(raw):
        return PeriodHit(role="total", period_key="total")
    if _STUB.fullmatch(raw):
        return PeriodHit(role="stub", period_key="stub")
    if _SCENARIO.fullmatch(raw):
        return PeriodHit(role="scenario", period_key=raw.lower())
    if _FACT.fullmatch(raw):
        return PeriodHit(role="historical", period_key="actual")
    if _PLAN.fullmatch(raw):
        return PeriodHit(role="forecast", period_key="plan")
    quarter = _QUARTER_RU.fullmatch(raw) or _QUARTER_EN.fullmatch(raw)
    if quarter:
        q, year, sfx = quarter.group(1), quarter.group(2), quarter.group(3)
        return PeriodHit(role=_suffix_role(sfx), period_key=f"{year}Q{q}")
    year = _YEAR.fullmatch(raw)
    if year:
        key = year.group(1) + (year.group(2) or "")
        return PeriodHit(role=_suffix_role(year.group(2)), period_key=key)
    as_year = _YEAR_FLOAT.fullmatch(raw)
    if as_year:
        return PeriodHit(role="historical", period_key=as_year.group(1))
    return None


def _suffix_role(suffix: str | None) -> ColumnRole:
    if not suffix:
        return "historical"
    if suffix.upper() in _FORECAST_SFX:
        return "forecast"
    return "historical"
