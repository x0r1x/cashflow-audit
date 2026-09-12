from __future__ import annotations

import logging

from cashflow_audit.checkers.models import Candidate, CheckDocument
from cashflow_audit.errors import PortError
from cashflow_audit.explain.conclusions import build_conclusions, headline_for
from cashflow_audit.explain.models import (
    ExplainCard,
    Finding,
    Provenance,
    Report,
    ReportSummary,
)
from cashflow_audit.explain.template import impact_text, template_card
from cashflow_audit.explain.verdict import build_positives, build_verdict
from cashflow_audit.frs.models import FrsDocument
from cashflow_audit.lineage.models import Impact, LineageDocument, LineageItem
from cashflow_audit.mapping.models import MappingDocument, MappingQuestion
from cashflow_audit.observability import log_event
from cashflow_audit.ports.protocols import ChatPort, SlotGate

_LOGGER = logging.getLogger(__name__)

FREEZE_TAGS = {"likely_intentional", "edge_period", "hist_manual_adjustment"}
_SEV_RANK = {"error": 0, "warning": 1, "risk": 2}
DETECTOR_VERSION = "1"


def compose_report(
    *,
    candidates: list[Candidate],
    lineage: LineageDocument,
    mapping: MappingDocument,
    check: CheckDocument,
    ir_refs: set[str],
    chat: ChatPort | None = None,
    slots: SlotGate | None = None,
    top_n: int = 20,
    embeddings_used: bool = False,
    audit_id: str = "",
    source_filename: str = "",
    sha256: str = "",
    llm_model: str | None = None,
    embedding_model: str | None = None,
    slot_timeout_sec: float = 0.0,
    frs: FrsDocument | None = None,
) -> Report:
    screen = frs or FrsDocument(controls=[], issues=[])
    lin_by_index = {item.candidate_index: item for item in lineage.items}
    kept: list[tuple[int, Candidate, LineageItem]] = []
    for index, cand in enumerate(candidates):
        if not cand.cell_refs or not any(ref in ir_refs for ref in cand.cell_refs):
            continue
        item = lin_by_index.get(index) or _empty_lineage(index, cand)
        kept.append((index, cand, item))

    order = sorted(
        range(len(kept)),
        key=lambda i: (_SEV_RANK.get(_severity(kept[i][1]), 9), i),
    )
    frs_order = [i for i in order if kept[i][1].detector.startswith("frs.")]
    llm_eligible = set(frs_order[:top_n])
    llm_used = False
    acquired = False
    if chat is not None and llm_eligible:
        acquired = _acquire(slots, slot_timeout_sec)
        if not acquired:
            log_event(
                _LOGGER,
                logging.WARNING,
                "port_fallback",
                "chat fallback",
                port="chat",
                reason="slot_timeout",
            )
    findings: list[Finding] = []
    for pos, (_index, cand, item) in enumerate(kept):
        card = template_card(cand, item)
        if chat is not None and acquired and pos in llm_eligible and _charge(slots, "llm"):
            overlay = _llm_card(chat, cand, item)
            if overlay is not None:
                card = overlay
                llm_used = True
        findings.append(
            Finding(
                id=f"f_{len(findings) + 1:03d}",
                severity=_severity(cand),
                detector=cand.detector,
                cell_refs=list(cand.cell_refs),
                title=card.title,
                evidence=card.evidence,
                affected_metrics=list(item.affected_metrics),
                impact=impact_text(item),
                recommendation=card.recommendation,
                tags=list(cand.tags),
                need_user_input=card.need_user_input,
            )
        )
    if acquired:
        _release(slots)

    _fill_related(findings, [item for _, _, item in kept])
    conclusions = build_conclusions(
        findings,
        [cand for _index, cand, _item in kept],
        [item for _index, _cand, item in kept],
    )

    questions = _merge_questions(mapping.questions, check.questions, findings)
    if questions:
        status = "needs_input"
    elif chat is not None and llm_eligible and not llm_used:
        status = "degraded"
    else:
        status = "succeeded"

    by_severity = {"error": 0, "warning": 0, "risk": 0}
    for finding in findings:
        by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1
    integrity = [item for item in findings if not item.detector.startswith("frs.")]

    return Report(
        audit_id=audit_id,
        source_filename=source_filename,
        sha256=sha256,
        status=status,
        llm_used=llm_used,
        embeddings_used=embeddings_used,
        summary=ReportSummary(
            findings=len(findings),
            by_severity=by_severity,
            questions=len(questions),
            headline=headline_for(conclusions, findings, questions),
        ),
        findings=findings,
        conclusions=conclusions,
        questions=questions,
        provenance=Provenance(
            detector_version=DETECTOR_VERSION,
            llm_model=llm_model if llm_used else None,
            embedding_model=embedding_model if embeddings_used else None,
        ),
        risk_screen=list(screen.controls),
        issues=list(screen.issues),
        positives=build_positives(screen),
        verdict=build_verdict(screen, integrity),
    )


def _severity(cand: Candidate) -> str:
    if FREEZE_TAGS.intersection(cand.tags) and cand.base_severity == "error":
        return "warning"
    return cand.base_severity


def _empty_lineage(index: int, cand: Candidate) -> LineageItem:
    return LineageItem(
        candidate_index=index,
        detector=cand.detector,
        cell_refs=list(cand.cell_refs),
        impact=Impact(kind="direction", value="downstream"),
    )


def _llm_card(chat: ChatPort, cand: Candidate, item: LineageItem) -> ExplainCard | None:
    allowed = set(cand.cell_refs) | set(item.path_refs) | set(item.output_refs)
    messages = [
        {
            "role": "system",
            "content": (
                "Write title, evidence, recommendation for an audit finding. "
                "Cite only provided cell refs. Do not change detector, metrics, or impact. "
                "Recommendation must say the file was not modified."
            ),
        },
        {
            "role": "user",
            "content": (
                f"detector: {cand.detector}\n"
                f"cell_refs: {', '.join(cand.cell_refs)}\n"
                f"metrics: {', '.join(item.affected_metrics)}\n"
                f"impact: {impact_text(item)}"
            ),
        },
    ]
    try:
        card = chat.complete_json(ExplainCard, messages)
    except PortError:
        log_event(
            _LOGGER,
            logging.WARNING,
            "port_fallback",
            "chat fallback",
            port="chat",
            reason="port_error",
        )
        return None
    except Exception:
        return None
    if not isinstance(card, ExplainCard):
        try:
            card = ExplainCard.model_validate(card.model_dump())
        except Exception:
            return None
    cited = list(getattr(card, "cited_refs", []) or [])
    if cited and not set(cited) <= allowed:
        return None
    if not card.title or not card.evidence or not card.recommendation:
        return None
    return card


def _fill_related(findings: list[Finding], items: list[LineageItem]) -> None:
    outputs = [set(item.output_refs) for item in items]
    for i, finding in enumerate(findings):
        related: list[str] = []
        for j, other in enumerate(findings):
            if i == j:
                continue
            if outputs[i] and outputs[i] & outputs[j]:
                related.append(other.id)
        finding.related_ids = related


def _merge_questions(
    mapping_qs: list[MappingQuestion],
    check_qs: list[MappingQuestion],
    findings: list[Finding],
) -> list[MappingQuestion]:
    merged: list[MappingQuestion] = []
    seen: set[tuple[str, frozenset[str]]] = set()
    extra = [
        MappingQuestion(
            id=f"q_ex_{finding.id}",
            kind="explain",
            prompt=finding.title,
            cell_refs=list(finding.cell_refs),
            options=["unknown"],
        )
        for finding in findings
        if finding.need_user_input
    ]
    for question in [*mapping_qs, *check_qs, *extra]:
        key = (question.kind, frozenset(question.cell_refs))
        if key in seen:
            continue
        seen.add(key)
        merged.append(question)
    return merged


def _acquire(slots: SlotGate | None, timeout_sec: float) -> bool:
    if slots is None:
        return True
    return slots.acquire("llm", timeout_sec)


def _release(slots: SlotGate | None) -> None:
    if slots is None:
        return
    slots.release("llm")


def _charge(slots: SlotGate | None, kind: str) -> bool:
    if slots is None:
        return True
    charge = getattr(slots, "charge", None)
    if charge is None:
        return True
    return bool(charge(kind))
