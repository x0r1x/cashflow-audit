from __future__ import annotations

from cashflow_audit.explain.models import Report, ReportSummary
from cashflow_audit.explain.risk_screen import build_risk_screen
from cashflow_audit.frs.catalog import CATALOG
from cashflow_audit.frs.models import ControlResult, FrsDocument, FrsIssue
from cashflow_audit.mapping.models import MappedRow, MappingDocument


def _mapped(*concept_ids: str) -> MappingDocument:
    return MappingDocument(
        rows=[
            MappedRow(
                row_key=f"Sheet!{index}:b1",
                sheet="Sheet",
                row=index,
                block_id="b1",
                label=f"row-{index}",
                concept_id=concept_id,
                article_role="output",
                source="rule",
            )
            for index, concept_id in enumerate(concept_ids, start=1)
        ]
    )


def test_every_control_has_complete_human_explanation() -> None:
    controls = [
        ControlResult(
            id=spec.id,
            name=spec.name,
            status="clear",
            confidence="high",
        )
        for spec in CATALOG
    ]

    rows = build_risk_screen(FrsDocument(controls=controls), MappingDocument())

    assert [row.id for row in rows] == [spec.id for spec in CATALOG]
    for row in rows:
        assert row.explanation.check
        assert row.explanation.status_reason
        assert row.explanation.key_fact
        assert row.explanation.impact
        assert row.explanation.next_step
        assert set(row.explanation.cited_refs) <= set(row.cell_refs)


def test_status_reasons_distinguish_flagged_insufficient_and_missing_mapping() -> None:
    controls = [
        ControlResult(
            id="F04",
            name="CFO/FCF",
            status="flagged",
            confidence="medium",
            metrics={"ni": 1200.0, "cfo": 400.0, "fcf": -100.0},
            cell_refs=["CF!E12"],
            issue_id="B-F04",
        ),
        ControlResult(
            id="F10",
            name="Процентный / валютный",
            status="insufficient",
            metrics={"fx": None},
            evidence="FX не рассчитан",
        ),
        ControlResult(
            id="F14",
            name="Headroom",
            status="not_applicable",
        ),
    ]
    mapping = _mapped("pnl.net_income", "pnl.interest")

    rows = build_risk_screen(FrsDocument(controls=controls), mapping)
    by_id = {row.id: row for row in rows}

    assert "B-F04" in by_id["F04"].explanation.status_reason
    assert "NI 1 200" in by_id["F04"].explanation.key_fact
    assert by_id["F04"].explanation.cited_refs == ["CF!E12"]
    assert "FX не рассчитан" in by_id["F10"].explanation.status_reason
    assert "bs.cash" in by_id["F14"].explanation.status_reason
    assert "covenant.headroom" not in by_id["F14"].explanation.status_reason


def test_percent_ratios_and_periods_are_formatted_for_analyst() -> None:
    controls = [
        ControlResult(
            id="F01",
            name="Выручка",
            status="flagged",
            confidence="high",
            metrics={"change": -0.154, "period_key": "2026"},
            cell_refs=["P&L!E3"],
            issue_id="B-F01",
        ),
        ControlResult(
            id="F06",
            name="Долг",
            status="clear",
            confidence="medium",
            metrics={"ratio": 2.1, "prev_ratio": 3.0, "period_key": "2026"},
        ),
    ]

    rows = build_risk_screen(FrsDocument(controls=controls), MappingDocument())
    facts = {row.id: row.explanation.key_fact for row in rows}

    assert "−15.4%" in facts["F01"]
    assert "2026" in facts["F01"]
    assert "2.1x" in facts["F06"]
    assert "3x" in facts["F06"]


def test_report_accepts_legacy_risk_rows_without_explanation() -> None:
    report = Report.model_validate(
        {
            "audit_id": "a",
            "source_filename": "synthetic.xlsx",
            "sha256": "s",
            "status": "succeeded",
            "llm_used": False,
            "embeddings_used": False,
            "summary": ReportSummary(
                findings=0,
                by_severity={"error": 0, "warning": 0, "risk": 0},
                questions=0,
            ).model_dump(),
            "findings": [],
            "risk_screen": [
                {
                    "id": "F01",
                    "name": "Выручка",
                    "status": "clear",
                    "confidence": "high",
                }
            ],
        }
    )

    assert report.risk_screen[0].explanation.check == ""
    assert report.risk_screen[0].explanation.cited_refs == []


def test_build_does_not_mutate_frs_document() -> None:
    issue = FrsIssue.model_validate(
        {
            "id": "B-F08",
            "control_id": "F08",
            "class_name": "liquidity",
            "priority": "high",
            "metrics": {"min_cash": -20.0, "period_key": "2025-03"},
            "cell_refs": ["BS!E10"],
        }
    )
    frs = FrsDocument(
        controls=[
            ControlResult(
                id="F08",
                name="Ликвидность",
                status="flagged",
                confidence="high",
                metrics=dict(issue.metrics),
                cell_refs=list(issue.cell_refs),
                issue_id=issue.id,
            )
        ],
        issues=[issue],
    )
    before = frs.model_dump()

    rows = build_risk_screen(frs, _mapped("bs.cash"))

    assert rows[0].explanation.key_fact
    assert frs.model_dump() == before
