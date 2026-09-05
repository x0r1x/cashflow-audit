from __future__ import annotations

from cashflow_audit.layout.periods import classify_header


def test_year_without_suffix_is_historical() -> None:
    hit = classify_header("2023")
    assert hit is not None
    assert hit.role == "historical"
    assert hit.period_key == "2023"


def test_year_with_e_is_forecast() -> None:
    hit = classify_header("2025E")
    assert hit is not None
    assert hit.role == "forecast"
    assert hit.period_key == "2025E"


def test_russian_quarter_is_period() -> None:
    hit = classify_header("1 кв. 2025")
    assert hit is not None
    assert hit.period_key == "2025Q1"


def test_fact_plan_keywords() -> None:
    assert classify_header("факт").role == "historical"  # type: ignore[union-attr]
    assert classify_header("план").role == "forecast"  # type: ignore[union-attr]


def test_scenario_and_total_and_stub() -> None:
    assert classify_header("Upside").role == "scenario"  # type: ignore[union-attr]
    assert classify_header("Итого").role == "total"  # type: ignore[union-attr]
    assert classify_header("stub").role == "stub"  # type: ignore[union-attr]


def test_line_item_is_not_a_period() -> None:
    assert classify_header("Revenue") is None
    assert classify_header("Выручка") is None
    assert classify_header("GMV") is None
