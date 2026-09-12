from __future__ import annotations

from collections.abc import Callable

from cashflow_audit.frs.catalog import CATALOG
from cashflow_audit.frs.context import FrsCtx
from cashflow_audit.frs.handlers import (
    handle_f01,
    handle_f02,
    handle_f03,
    handle_f04,
    handle_f05,
    handle_f06,
    handle_f07,
    handle_f08,
    handle_f09,
    handle_f10,
    handle_f11,
    handle_f12,
    handle_f13,
    handle_f14,
)
from cashflow_audit.frs.models import ControlResult, FrsDocument, FrsIssue
from cashflow_audit.layout.models import Layout
from cashflow_audit.mapping.models import MappingDocument

Handler = Callable[[FrsCtx, str, str], tuple[ControlResult, FrsIssue | None]]

HANDLERS: dict[str, Handler] = {
    "F01": handle_f01,
    "F02": handle_f02,
    "F03": handle_f03,
    "F04": handle_f04,
    "F05": handle_f05,
    "F06": handle_f06,
    "F07": handle_f07,
    "F08": handle_f08,
    "F09": handle_f09,
    "F10": handle_f10,
    "F11": handle_f11,
    "F12": handle_f12,
    "F13": handle_f13,
    "F14": handle_f14,
}


_CONF_RANK = {"low": 0, "medium": 1, "high": 2}
_IDENTITY_CAP = {
    "identity.I1": ("F06", "F08"),
    "identity.I3a": ("F04", "F08"),
    "identity.I3b": ("F13",),
}


def run_frs(
    mapping: MappingDocument,
    *,
    layout: Layout | None = None,
    cells: list[dict] | None = None,
    identity: frozenset[str] | None = None,
) -> FrsDocument:
    have = {row.concept_id for row in mapping.rows if row.concept_id}
    ctx = FrsCtx(mapping=mapping, layout=layout, cells=cells or [])
    controls: list[ControlResult] = []
    issues: list[FrsIssue] = []
    for spec in CATALOG:
        if not have or not spec.need.issubset(have):
            controls.append(
                ControlResult(id=spec.id, name=spec.name, status="not_applicable")
            )
            continue
        handler = HANDLERS.get(spec.id)
        if handler is None:
            controls.append(
                ControlResult(id=spec.id, name=spec.name, status="insufficient")
            )
            continue
        result, issue = handler(ctx, spec.id, spec.name)
        controls.append(result)
        if issue is not None:
            issues.append(issue)
    return _with_confidence(
        FrsDocument(controls=controls, issues=issues),
        identity or frozenset(),
    )


def _with_confidence(doc: FrsDocument, identity: frozenset[str]) -> FrsDocument:
    caps: dict[str, str] = {}
    for detector, ids in _IDENTITY_CAP.items():
        if detector not in identity:
            continue
        for fid in ids:
            caps[fid] = "low"
    for control in doc.controls:
        if control.status not in {"clear", "flagged"}:
            continue
        if control.confidence is None:
            control.confidence = "high"
        cap = caps.get(control.id)
        if cap:
            control.confidence = _worse(control.confidence, cap)
    return doc


def _worse(current: str, cap: str) -> str:
    if _CONF_RANK[current] <= _CONF_RANK[cap]:
        return current
    return cap
