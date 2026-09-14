from __future__ import annotations

import logging

from cashflow_audit.checkers.models import Candidate, CheckDocument
from cashflow_audit.errors import PortError
from cashflow_audit.explain.compose import compose_report
from cashflow_audit.frs.models import ControlResult, FrsDocument, FrsIssue
from cashflow_audit.lineage.models import Impact, LineageDocument, LineageItem
from cashflow_audit.mapping.models import MappingDocument, MappingQuestion
from tests.helpers.ports import CapBudget, DenySlots, FakeChat, GrantSlots


def _cand(**kwargs) -> Candidate:
    base = dict(
        detector="excel_error",
        cell_refs=["P&L!B2"],
        tags=[],
        payload={},
        base_severity="error",
    )
    base.update(kwargs)
    return Candidate.model_validate(base)


def _lin(index: int = 0, **kwargs) -> LineageItem:
    base = dict(
        candidate_index=index,
        detector="excel_error",
        cell_refs=["P&L!B2"],
        output_refs=["P&L!C3"],
        affected_metrics=["pnl.ebitda"],
        impact=Impact(kind="direction", value="downstream"),
        truncated=False,
        complete=True,
        path_refs=["P&L!B2", "P&L!C3"],
    )
    base.update(kwargs)
    return LineageItem.model_validate(base)


def _issue(control_id: str = "F04", **kwargs) -> FrsIssue:
    base: dict = {
        "id": f"B-{control_id}",
        "control_id": control_id,
        "class_name": "cash_conversion",
        "priority": "medium",
        "metrics": {"ni": 1200.0, "cfo": 400.0},
        "cell_refs": ["P&L!B2"],
        "cause": "ops",
        "impact": "",
    }
    base.update(kwargs)
    return FrsIssue.model_validate(base)


def _frs(*issues: FrsIssue) -> FrsDocument:
    controls = [
        ControlResult(
            id=issue.control_id,
            name=issue.control_id,
            status="flagged",
            cell_refs=list(issue.cell_refs),
            metrics=dict(issue.metrics),
            issue_id=issue.id,
        )
        for issue in issues
    ]
    return FrsDocument(controls=controls, issues=list(issues))


def test_template_is_always_complete() -> None:
    report = compose_report(
        candidates=[_cand()],
        lineage=LineageDocument(items=[_lin()]),
        mapping=MappingDocument(),
        check=CheckDocument(),
        ir_refs={"P&L!B2", "P&L!C3"},
        chat=None,
        slots=GrantSlots(),
    )
    assert report.findings == []
    finding = report.integrity_findings[0]
    assert finding.title
    assert finding.evidence
    assert finding.recommendation
    rec = finding.recommendation.casefold()
    assert "не изменён" in rec or "не изменен" in rec


def test_chat_skips_excel_error_cards() -> None:
    chat = FakeChat(
        payload={"cause": "LLM", "impact": "e", "cited_refs": []}
    )
    report = compose_report(
        candidates=[_cand()],
        lineage=LineageDocument(items=[_lin()]),
        mapping=MappingDocument(),
        check=CheckDocument(),
        ir_refs={"P&L!B2", "P&L!C3"},
        chat=chat,
        slots=GrantSlots(),
    )
    assert chat.calls == 0
    assert report.status == "succeeded"
    assert report.findings == []
    assert report.integrity_findings[0].detector == "excel_error"
    assert report.verdict.ready_for_credit is False


def test_configured_chat_without_findings_is_succeeded() -> None:
    chat = FakeChat(payload={"cause": "LLM", "impact": "e", "cited_refs": []})
    report = compose_report(
        candidates=[],
        lineage=LineageDocument(),
        mapping=MappingDocument(),
        check=CheckDocument(),
        ir_refs={"P&L!B2"},
        chat=chat,
        slots=GrantSlots(),
    )
    assert report.status == "succeeded"
    assert report.llm_used is False
    assert report.findings == []
    assert report.integrity_findings == []
    assert chat.calls == 0


def test_llm_rewrites_issue_prose_not_numbers() -> None:
    chat = FakeChat(
        payload={
            "cause": "Прибыль не конвертируется в кэш",
            "impact": "FCF отстаёт при NI 1200",
            "need_user_input": False,
            "cited_refs": ["P&L!B2"],
            "detector": "hacked",
            "cell_refs": ["Hack!Z9"],
            "metrics": {"ni": 1},
            "priority": "high",
        }
    )
    issue = _issue()
    report = compose_report(
        candidates=[_cand(detector="frs.F04", payload={"delta": 1200})],
        lineage=LineageDocument(
            items=[
                _lin(
                    impact=Impact(kind="numeric", value=1200.0),
                    affected_metrics=["pnl.ebitda"],
                    detector="frs.F04",
                )
            ]
        ),
        mapping=MappingDocument(),
        check=CheckDocument(),
        ir_refs={"P&L!B2", "P&L!C3"},
        chat=chat,
        slots=GrantSlots(),
        frs=_frs(issue),
    )
    assert report.findings == []
    got = report.issues[0]
    assert got.id == "B-F04"
    assert got.control_id == "F04"
    assert got.cell_refs == ["P&L!B2"]
    assert got.metrics["ni"] == 1200.0
    assert got.priority == "medium"
    assert got.cause == "Прибыль не конвертируется в кэш"
    assert "1200" in got.impact
    assert chat.calls == 1


def test_cited_refs_outside_input_falls_back_to_template() -> None:
    chat = FakeChat(
        payload={
            "cause": "LLM-ONLY-CAUSE",
            "impact": "LLM-ONLY-IMPACT",
            "cited_refs": ["Other!Z9"],
        }
    )
    report = compose_report(
        candidates=[_cand(detector="frs.F04")],
        lineage=LineageDocument(items=[_lin(detector="frs.F04")]),
        mapping=MappingDocument(),
        check=CheckDocument(),
        ir_refs={"P&L!B2", "P&L!C3"},
        chat=chat,
        slots=GrantSlots(),
        frs=_frs(_issue()),
    )
    assert report.issues[0].cause != "LLM-ONLY-CAUSE"
    assert "операц" in report.issues[0].cause.casefold()
    assert report.issues[0].impact


def test_llm_invented_number_falls_back_to_template() -> None:
    chat = FakeChat(
        payload={
            "cause": "Разрыв 9999",
            "impact": "выдуманная цифра",
            "cited_refs": ["P&L!B2"],
        }
    )
    report = compose_report(
        candidates=[_cand(detector="frs.F04")],
        lineage=LineageDocument(items=[_lin(detector="frs.F04")]),
        mapping=MappingDocument(),
        check=CheckDocument(),
        ir_refs={"P&L!B2", "P&L!C3"},
        chat=chat,
        slots=GrantSlots(),
        frs=_frs(_issue()),
    )
    assert "операц" in report.issues[0].cause.casefold()
    assert report.issues[0].impact
    assert "9999" not in report.issues[0].cause


def test_flagged_issue_has_complete_template_without_chat() -> None:
    report = compose_report(
        candidates=[_cand(detector="frs.F04")],
        lineage=LineageDocument(items=[_lin(detector="frs.F04")]),
        mapping=MappingDocument(),
        check=CheckDocument(),
        ir_refs={"P&L!B2", "P&L!C3"},
        chat=None,
        slots=GrantSlots(),
        frs=_frs(_issue()),
    )

    assert report.issues[0].cause
    assert report.issues[0].impact
    assert report.risk_screen[0].explanation.status_reason
    assert report.llm_used is False
    assert report.status == "succeeded"


def test_drop_finding_without_ir_ref() -> None:
    report = compose_report(
        candidates=[_cand(cell_refs=["Ghost!A1"])],
        lineage=LineageDocument(items=[_lin(cell_refs=["Ghost!A1"])]),
        mapping=MappingDocument(),
        check=CheckDocument(),
        ir_refs={"P&L!B2"},
        chat=None,
        slots=GrantSlots(),
    )
    assert report.findings == []
    assert report.integrity_findings == []


def test_freeze_tags_cap_severity_at_warning() -> None:
    report = compose_report(
        candidates=[_cand(tags=["likely_intentional"], base_severity="error")],
        lineage=LineageDocument(items=[_lin()]),
        mapping=MappingDocument(),
        check=CheckDocument(),
        ir_refs={"P&L!B2", "P&L!C3"},
        chat=None,
        slots=GrantSlots(),
    )
    assert report.integrity_findings[0].severity == "warning"


def test_questions_union_and_dedup() -> None:
    q = MappingQuestion(
        id="q_001",
        kind="mapping",
        prompt="x",
        cell_refs=["P&L!A2"],
        options=["pnl.revenue", "unknown"],
    )
    dup = MappingQuestion(
        id="q_999",
        kind="mapping",
        prompt="other",
        cell_refs=["P&L!A2"],
        options=["unknown"],
    )
    ident = MappingQuestion(
        id="q_id_001",
        kind="identity_gap",
        prompt="gap",
        cell_refs=["P&L!A2"],
        options=["bs.assets_total", "unknown"],
    )
    report = compose_report(
        candidates=[_cand()],
        lineage=LineageDocument(items=[_lin()]),
        mapping=MappingDocument(questions=[q, dup]),
        check=CheckDocument(questions=[ident]),
        ir_refs={"P&L!B2", "P&L!C3"},
        chat=None,
        slots=GrantSlots(),
    )
    keys = {(item.kind, tuple(item.cell_refs)) for item in report.questions}
    assert ("mapping", ("P&L!A2",)) in keys
    assert ("identity_gap", ("P&L!A2",)) in keys
    assert len([x for x in report.questions if x.kind == "mapping"]) == 1
    assert report.status == "needs_input"


def test_related_ids_share_downstream_output() -> None:
    c1 = _cand(cell_refs=["P&L!B2"])
    c2 = _cand(detector="pattern_break", cell_refs=["P&L!B4"])
    report = compose_report(
        candidates=[c1, c2],
        lineage=LineageDocument(
            items=[
                _lin(0, cell_refs=["P&L!B2"], output_refs=["P&L!C3"]),
                _lin(
                    1,
                    detector="pattern_break",
                    cell_refs=["P&L!B4"],
                    output_refs=["P&L!C3"],
                    path_refs=["P&L!B4", "P&L!C3"],
                ),
            ]
        ),
        mapping=MappingDocument(),
        check=CheckDocument(),
        ir_refs={"P&L!B2", "P&L!B4", "P&L!C3"},
        chat=None,
        slots=GrantSlots(),
    )
    left, right = report.integrity_findings
    assert right.id in left.related_ids
    assert left.id in right.related_ids


def test_denied_slot_does_not_call_chat_and_uses_template(caplog) -> None:
    chat = FakeChat(payload={"cause": "LLM", "impact": "e", "cited_refs": []})
    caplog.set_level(logging.INFO, logger="cashflow_audit")
    report = compose_report(
        candidates=[_cand(detector="frs.F04")],
        lineage=LineageDocument(items=[_lin(detector="frs.F04")]),
        mapping=MappingDocument(),
        check=CheckDocument(),
        ir_refs={"P&L!B2", "P&L!C3"},
        chat=chat,
        slots=DenySlots(),
        frs=_frs(_issue()),
    )
    assert chat.calls == 0
    assert "операц" in report.issues[0].cause.casefold()
    assert report.issues[0].impact
    assert report.status == "degraded"
    assert report.llm_used is False
    assert any(
        r.__dict__.get("event") == "port_fallback"
        and r.__dict__.get("reason") == "slot_timeout"
        and r.__dict__.get("port") == "chat"
        for r in caplog.records
    )


def test_chat_port_error_logs_port_fallback(caplog) -> None:
    class BoomChat:
        def complete_json(self, schema, messages):
            raise PortError("chat down")

    caplog.set_level(logging.INFO, logger="cashflow_audit")
    report = compose_report(
        candidates=[_cand(detector="frs.F04")],
        lineage=LineageDocument(items=[_lin(detector="frs.F04")]),
        mapping=MappingDocument(),
        check=CheckDocument(),
        ir_refs={"P&L!B2", "P&L!C3"},
        chat=BoomChat(),
        slots=GrantSlots(),
        frs=_frs(_issue()),
    )
    assert "операц" in report.issues[0].cause.casefold()
    assert report.issues[0].impact
    assert report.status == "degraded"
    assert any(
        r.__dict__.get("event") == "port_fallback"
        and r.__dict__.get("reason") == "port_error"
        and r.__dict__.get("port") == "chat"
        for r in caplog.records
    )


def test_explain_stops_llm_when_budget_exhausted() -> None:
    chat = FakeChat(
        payload={
            "cause": "LLM cause",
            "impact": "LLM impact 1200",
            "cited_refs": ["P&L!B2"],
        }
    )
    first = _issue("F04")
    second = _issue("F08", cell_refs=["P&L!C2"], metrics={"min_cash": 1.0}, cause="cash_plug")
    report = compose_report(
        candidates=[
            _cand(detector="frs.F04"),
            _cand(cell_refs=["P&L!C2"], detector="frs.F08"),
        ],
        lineage=LineageDocument(
            items=[
                _lin(0, detector="frs.F04"),
                _lin(1, detector="frs.F08", cell_refs=["P&L!C2"], path_refs=["P&L!C2"]),
            ]
        ),
        mapping=MappingDocument(),
        check=CheckDocument(),
        ir_refs={"P&L!B2", "P&L!C2", "P&L!C3"},
        chat=chat,
        slots=CapBudget({"llm": 1}),
        frs=_frs(first, second),
    )
    assert chat.calls == 1
    assert report.findings == []
    assert len(report.issues) == 2
    assert report.issues[0].cause == "LLM cause"
    assert "cash plug" in report.issues[1].cause.casefold()
    assert report.issues[1].impact


def test_headline_cites_integrity_error_over_high_issue() -> None:
    high = _issue("F08", priority="high")
    report = compose_report(
        candidates=[
            _cand(detector="identity.I1", cell_refs=["BS!E27"]),
            _cand(detector="frs.F08", cell_refs=["P&L!B2"]),
        ],
        lineage=LineageDocument(
            items=[
                _lin(
                    0,
                    detector="identity.I1",
                    cell_refs=["BS!E27"],
                    output_refs=["BS!E27"],
                    affected_metrics=["bs.assets"],
                    path_refs=["BS!E27"],
                ),
                _lin(1, detector="frs.F08"),
            ]
        ),
        mapping=MappingDocument(),
        check=CheckDocument(),
        ir_refs={"BS!E27", "P&L!B2", "P&L!C3"},
        chat=None,
        slots=GrantSlots(),
        frs=_frs(high),
    )
    assert "f_001" in report.summary.headline
    assert "B-F08" not in report.summary.headline
