from __future__ import annotations

import logging
import re
from typing import Any

from cashflow_audit.checkers.models import Candidate, CheckDocument
from cashflow_audit.errors import PortError
from cashflow_audit.explain.conclusions import build_conclusions, headline_for
from cashflow_audit.explain.models import (
    Finding,
    IssueProse,
    Provenance,
    Report,
    ReportSummary,
)
from cashflow_audit.explain.template import impact_text, template_card
from cashflow_audit.explain.verdict import build_positives, build_verdict
from cashflow_audit.frs.models import FrsDocument, FrsIssue
from cashflow_audit.lineage.models import Impact, LineageDocument, LineageItem
from cashflow_audit.mapping.models import MappingDocument, MappingQuestion
from cashflow_audit.observability import log_event
from cashflow_audit.ports.protocols import ChatPort, SlotGate

_LOGGER = logging.getLogger(__name__)

FREEZE_TAGS = {"likely_intentional", "edge_period", "hist_manual_adjustment"}
_PRIO = {"high": 0, "medium": 1, "low": 2}
DETECTOR_VERSION = "1"
_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


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
    issues = [item.model_copy(deep=True) for item in screen.issues]
    lin_by_index = {item.candidate_index: item for item in lineage.items}
    integrity_kept: list[tuple[int, Candidate, LineageItem]] = []
    frs_kept: list[tuple[int, Candidate, LineageItem]] = []
    for index, cand in enumerate(candidates):
        if not cand.cell_refs or not any(ref in ir_refs for ref in cand.cell_refs):
            continue
        item = lin_by_index.get(index) or _empty_lineage(index, cand)
        if cand.detector.startswith("frs."):
            frs_kept.append((index, cand, item))
        else:
            integrity_kept.append((index, cand, item))

    ranked = sorted(range(len(issues)), key=lambda i: (_PRIO.get(issues[i].priority, 9), i))
    llm_eligible = {i for i in ranked[:top_n] if issues[i].cell_refs}
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
    if chat is not None and acquired:
        for index in ranked:
            if index not in llm_eligible:
                continue
            if not _charge(slots, "llm"):
                continue
            overlay = _llm_issue(chat, issues[index])
            if overlay is not None:
                issues[index] = overlay
                llm_used = True
        _release(slots)

    integrity_findings: list[Finding] = []
    for _index, cand, item in integrity_kept:
        card = template_card(cand, item)
        integrity_findings.append(
            Finding(
                id=f"f_{len(integrity_findings) + 1:03d}",
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
    _fill_related(integrity_findings, [item for _, _, item in integrity_kept])

    frs_findings: list[Finding] = []
    for _index, cand, item in frs_kept:
        frs_findings.append(
            Finding(
                id=_issue_id_for(cand, issues),
                severity=_severity(cand),
                detector=cand.detector,
                cell_refs=list(cand.cell_refs),
                title="",
                evidence="",
                affected_metrics=list(item.affected_metrics),
                impact=impact_text(item),
                recommendation="",
                tags=list(cand.tags),
            )
        )

    conclusions = build_conclusions(
        [*integrity_findings, *frs_findings],
        [cand for _index, cand, _item in integrity_kept]
        + [cand for _index, cand, _item in frs_kept],
        [item for _index, _cand, item in integrity_kept]
        + [item for _index, _cand, item in frs_kept],
    )

    questions = _merge_questions(mapping.questions, check.questions, integrity_findings)
    if questions:
        status = "needs_input"
    elif chat is not None and llm_eligible and not llm_used:
        status = "degraded"
    else:
        status = "succeeded"

    return Report(
        audit_id=audit_id,
        source_filename=source_filename,
        sha256=sha256,
        status=status,
        llm_used=llm_used,
        embeddings_used=embeddings_used,
        summary=ReportSummary(
            findings=0,
            by_severity={"error": 0, "warning": 0, "risk": 0},
            questions=len(questions),
            headline=headline_for(
                conclusions, integrity_findings, questions, issues=issues
            ),
        ),
        findings=[],
        conclusions=conclusions,
        questions=questions,
        provenance=Provenance(
            detector_version=DETECTOR_VERSION,
            llm_model=llm_model if llm_used else None,
            embedding_model=embedding_model if embeddings_used else None,
        ),
        risk_screen=list(screen.controls),
        issues=issues,
        positives=build_positives(screen),
        verdict=build_verdict(screen, integrity_findings),
        integrity_findings=integrity_findings,
    )


def _issue_id_for(cand: Candidate, issues: list[FrsIssue]) -> str:
    cid = cand.detector.removeprefix("frs.")
    cand_refs = set(cand.cell_refs)
    for issue in issues:
        if issue.control_id == cid and set(issue.cell_refs) == cand_refs:
            return issue.id
    for issue in issues:
        if issue.control_id == cid:
            return issue.id
    return f"B-{cid}"


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


def _llm_issue(chat: ChatPort, issue: FrsIssue) -> FrsIssue | None:
    allowed_refs = set(issue.cell_refs)
    allowed_nums = _floats_from(issue.cause, issue.impact) + _floats_from_metrics(
        issue.metrics
    )
    messages = [
        {
            "role": "system",
            "content": (
                "Rewrite cause and impact as analyst prose for a flagged FRS issue. "
                "Do not change numbers. Do not set F-row status or a credit verdict. "
                "Cite only provided cell refs."
            ),
        },
        {
            "role": "user",
            "content": (
                f"control_id: {issue.control_id}\n"
                f"issue_id: {issue.id}\n"
                f"cell_refs: {', '.join(issue.cell_refs)}\n"
                f"metrics: {issue.metrics}\n"
                f"cause: {issue.cause}\n"
                f"impact: {issue.impact}"
            ),
        },
    ]
    try:
        card = chat.complete_json(IssueProse, messages)
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
    if not isinstance(card, IssueProse):
        try:
            card = IssueProse.model_validate(card.model_dump())
        except Exception:
            return None
    cited = list(getattr(card, "cited_refs", []) or [])
    if cited and not set(cited) <= allowed_refs:
        return None
    if not card.cause or not card.impact:
        return None
    if not _numbers_allowed(f"{card.cause} {card.impact}", allowed_nums):
        return None
    return issue.model_copy(update={"cause": card.cause, "impact": card.impact})


def _floats_from(*texts: str) -> list[float]:
    found: list[float] = []
    for text in texts:
        for raw in _NUM_RE.findall(text or ""):
            try:
                found.append(float(raw.replace(",", ".")))
            except ValueError:
                continue
    return found


def _floats_from_metrics(metrics: dict[str, Any]) -> list[float]:
    found: list[float] = []
    for value in metrics.values():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            found.append(float(value))
        elif isinstance(value, str):
            found.extend(_floats_from(value))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, (int, float)) and not isinstance(item, bool):
                    found.append(float(item))
                elif isinstance(item, str):
                    found.extend(_floats_from(item))
    return found


def _numbers_allowed(text: str, allowed: list[float]) -> bool:
    for raw in _NUM_RE.findall(text or ""):
        try:
            number = float(raw.replace(",", "."))
        except ValueError:
            continue
        if not any(_close(number, item) for item in allowed):
            return False
    return True


def _close(left: float, right: float) -> bool:
    return abs(left - right) <= max(1e-6, 1e-6 * max(abs(left), abs(right)))


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
