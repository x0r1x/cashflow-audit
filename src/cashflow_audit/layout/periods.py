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
_ISO_YM = re.compile(r"^((?:19|20)\d{2})-(\d{2})([EFAPеЕфФпП])?$")
_NAMED_MONTH = re.compile(
    r"^([A-Za-zА-Яа-яёЁ]+)\.?\s*[-/.]?\s*((?:19|20)\d{2}|\d{2})([EFAPеЕфФпП])?$",
    re.IGNORECASE,
)
_MONTH_NUM = {
    "янв": 1,
    "январ": 1,
    "фев": 2,
    "феврал": 2,
    "мар": 3,
    "март": 3,
    "апр": 4,
    "апрел": 4,
    "май": 5,
    "мая": 5,
    "июн": 6,
    "июня": 6,
    "июл": 7,
    "июля": 7,
    "авг": 8,
    "август": 8,
    "сен": 9,
    "сентябр": 9,
    "окт": 10,
    "октябр": 10,
    "ноя": 11,
    "ноябр": 11,
    "дек": 12,
    "декабр": 12,
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


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
    iso = _ISO_YM.fullmatch(raw)
    if iso:
        month = int(iso.group(2))
        if 1 <= month <= 12:
            return PeriodHit(
                role=_suffix_role(iso.group(3)),
                period_key=f"{iso.group(1)}-{month:02d}",
            )
    named = _month_hit(raw)
    if named is not None:
        return named
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


def _month_hit(raw: str) -> PeriodHit | None:
    match = _NAMED_MONTH.fullmatch(raw)
    if match is None:
        return None
    month = _month_number(match.group(1))
    if month is None:
        return None
    year = _full_year(match.group(2))
    return PeriodHit(
        role=_suffix_role(match.group(3)),
        period_key=f"{year}-{month:02d}",
    )


def _month_number(token: str) -> int | None:
    stem = token.lower().replace("ё", "е").rstrip(".")
    found: int | None = None
    best = 0
    for name, num in _MONTH_NUM.items():
        if stem == name or stem.startswith(name):
            if len(name) > best:
                found = num
                best = len(name)
    return found


def _full_year(raw: str) -> str:
    if len(raw) == 2:
        return f"20{raw}"
    return raw


def _suffix_role(suffix: str | None) -> ColumnRole:
    if not suffix:
        return "historical"
    if suffix.upper() in _FORECAST_SFX:
        return "forecast"
    return "historical"
