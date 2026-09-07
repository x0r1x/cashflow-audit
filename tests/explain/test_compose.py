from __future__ import annotations

import logging

from cashflow_audit.checkers.models import Candidate, CheckDocument
from cashflow_audit.errors import PortError
from cashflow_audit.explain.compose import compose_report
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
    finding = report.findings[0]
    assert finding.title
    assert finding.evidence
    assert finding.recommendation
    rec = finding.recommendation.casefold()
    assert "не изменён" in rec or "не изменен" in rec


def test_llm_does_not_change_detector_refs_metrics_impact() -> None:
    chat = FakeChat(
        payload={
            "title": "LLM title",
            "evidence": "LLM evidence P&L!B2",
            "recommendation": "Проверить. Файл не изменён.",
            "need_user_input": False,
            "cited_refs": ["P&L!B2"],
            "detector": "hacked",
            "cell_refs": ["Hack!Z9"],
            "affected_metrics": ["fcf"],
            "impact": "nope",
        }
    )
    report = compose_report(
        candidates=[_cand(payload={"delta": 1200})],
        lineage=LineageDocument(
            items=[
                _lin(
                    impact=Impact(kind="numeric", value=1200.0),
                    affected_metrics=["pnl.ebitda"],
                )
            ]
        ),
        mapping=MappingDocument(),
        check=CheckDocument(),
        ir_refs={"P&L!B2", "P&L!C3"},
        chat=chat,
        slots=GrantSlots(),
    )
    finding = report.findings[0]
    assert finding.detector == "excel_error"
    assert finding.cell_refs == ["P&L!B2"]
    assert finding.affected_metrics == ["pnl.ebitda"]
    assert "1200" in finding.impact
    assert finding.title == "LLM title"
    assert chat.calls == 1


def test_cited_refs_outside_input_falls_back_to_template() -> None:
    chat = FakeChat(
        payload={
            "title": "LLM-ONLY-TITLE",
            "evidence": "x",
            "recommendation": "y. Файл не изменён.",
            "need_user_input": False,
            "cited_refs": ["Other!Z9"],
        }
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
    assert report.findings[0].title != "LLM-ONLY-TITLE"
    assert report.findings[0].title


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
    assert report.findings[0].severity == "warning"


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
    assert report.findings[1].id in report.findings[0].related_ids
    assert report.findings[0].id in report.findings[1].related_ids


def test_denied_slot_does_not_call_chat_and_uses_template(caplog) -> None:
    chat = FakeChat(
        payload={"title": "LLM", "evidence": "e", "recommendation": "r", "cited_refs": []}
    )
    caplog.set_level(logging.INFO, logger="cashflow_audit")
    report = compose_report(
        candidates=[_cand()],
        lineage=LineageDocument(items=[_lin()]),
        mapping=MappingDocument(),
        check=CheckDocument(),
        ir_refs={"P&L!B2", "P&L!C3"},
        chat=chat,
        slots=DenySlots(),
    )
    assert chat.calls == 0
    assert report.findings[0].title != "LLM"
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
        candidates=[_cand()],
        lineage=LineageDocument(items=[_lin()]),
        mapping=MappingDocument(),
        check=CheckDocument(),
        ir_refs={"P&L!B2", "P&L!C3"},
        chat=BoomChat(),
        slots=GrantSlots(),
    )
    assert report.findings[0].title
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
            "title": "LLM title",
            "evidence": "LLM evidence P&L!B2",
            "recommendation": "Проверить. Файл не изменён.",
            "need_user_input": False,
            "cited_refs": ["P&L!B2"],
        }
    )
    report = compose_report(
        candidates=[
            _cand(),
            _cand(cell_refs=["P&L!C2"], detector="hidden_input"),
        ],
        lineage=LineageDocument(
            items=[
                _lin(0),
                _lin(1, detector="hidden_input", cell_refs=["P&L!C2"], path_refs=["P&L!C2"]),
            ]
        ),
        mapping=MappingDocument(),
        check=CheckDocument(),
        ir_refs={"P&L!B2", "P&L!C2", "P&L!C3"},
        chat=chat,
        slots=CapBudget({"llm": 1}),
    )
    assert chat.calls == 1
    assert len(report.findings) == 2
    assert report.findings[0].title == "LLM title"
    assert report.findings[1].title != "LLM title"
    assert report.findings[1].title
    assert report.findings[1].evidence
