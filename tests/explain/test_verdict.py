from __future__ import annotations

from cashflow_audit.explain.models import Finding
from cashflow_audit.explain.verdict import build_positives, build_verdict
from cashflow_audit.frs.models import ControlResult, FrsDocument, FrsIssue


def _control(cid: str, status: str, **kwargs) -> ControlResult:
    base: dict = {"id": cid, "name": cid, "status": status}
    base.update(kwargs)
    return ControlResult.model_validate(base)


def _issue(cid: str = "F04", *, priority: str = "medium") -> FrsIssue:
    return FrsIssue.model_validate(
        {
            "id": f"B-{cid}",
            "control_id": cid,
            "class_name": "cash_conversion",
            "priority": priority,
            "cell_refs": ["CF!E12"],
        }
    )


def test_f04_flagged_trends_start_with_profit_not_cash() -> None:
    frs = FrsDocument(
        controls=[
            _control("F04", "flagged", issue_id="B-F04"),
            _control("F01", "flagged", issue_id="B-F01"),
        ],
        issues=[_issue("F04"), _issue("F01", priority="low")],
    )
    verdict = build_verdict(frs, [])
    assert verdict.trends.startswith("Прибыль не равна деньгам")


def test_liquidity_includes_f08_runway_and_f09() -> None:
    frs = FrsDocument(
        controls=[
            _control(
                "F08",
                "flagged",
                metrics={"min_cash": -12.0, "runway": 2, "period_key": "2025-03"},
                issue_id="B-F08",
            ),
            _control(
                "F09",
                "flagged",
                metrics={"share": 0.42, "year": "2026"},
                issue_id="B-F09",
            ),
        ],
        issues=[_issue("F08", priority="high"), _issue("F09")],
    )
    text = build_verdict(frs, []).liquidity
    assert "2" in text
    assert "runway" in text.casefold() or "период" in text.casefold()
    assert "F09" in text
    assert "2026" in text or "42" in text


def test_liquidity_says_cash_does_not_run_out() -> None:
    frs = FrsDocument(
        controls=[
            _control(
                "F08",
                "clear",
                metrics={"min_cash": 60.0, "period_key": "2025-03"},
                evidence="не иссякает",
            ),
            _control("F09", "not_applicable"),
        ]
    )
    text = build_verdict(frs, []).liquidity
    assert "не иссякает" in text
    assert "runway" not in text.casefold()


def test_positives_from_clear_controls_with_substance() -> None:
    frs = FrsDocument(
        controls=[
            _control("F02", "clear", metrics={"margin": 0.18}),
            _control(
                "F06",
                "clear",
                metrics={"ratio": 2.1, "prev_ratio": 3.0},
            ),
            _control("F07", "clear", metrics={"icr": 4.5}),
            _control(
                "F08",
                "clear",
                metrics={"min_cash": 50.0, "period_key": "2025-03"},
            ),
            _control("F05", "insufficient"),
            _control("F03", "not_applicable"),
        ]
    )
    positives = build_positives(frs)
    by_id = {item.control_id: item.text for item in positives}
    assert set(by_id) == {"F02", "F06", "F07", "F08"}
    assert "марж" in by_id["F02"].casefold()
    assert "0.18" in by_id["F02"] or "18" in by_id["F02"]
    assert "ND/EBITDA" in by_id["F06"] or "nd/ebitda" in by_id["F06"].casefold()
    assert "3" in by_id["F06"] and "2.1" in by_id["F06"]
    assert "ICR" in by_id["F07"] or "icr" in by_id["F07"].casefold()
    assert "4.5" in by_id["F07"]
    assert "50" in by_id["F08"]
    assert "min cash" in by_id["F08"].casefold() or "касс" in by_id["F08"].casefold()


def test_no_positive_for_insufficient_and_empty_ok() -> None:
    empty = build_positives(FrsDocument(controls=[]))
    assert empty == []
    only_na = build_positives(
        FrsDocument(controls=[_control("F02", "insufficient"), _control("F08", "flagged")])
    )
    assert only_na == []


def test_identity_error_sets_ready_false() -> None:
    ident = Finding(
        id="f_001",
        severity="error",
        detector="identity.I1",
        cell_refs=["BS!E27"],
        title="Баланс не сходится",
        evidence="x",
        impact="y",
        recommendation="z",
    )
    verdict = build_verdict(FrsDocument(controls=[]), [ident])
    assert verdict.ready_for_credit is False
    assert "целостн" in verdict.integrity.casefold() or "наруш" in verdict.integrity.casefold()
