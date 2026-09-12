from __future__ import annotations

from cashflow_audit.frs.catalog import CATALOG
from cashflow_audit.frs.models import ControlResult, FrsDocument
from cashflow_audit.mapping.models import MappingDocument


def run_frs(mapping: MappingDocument) -> FrsDocument:
    have = {row.concept_id for row in mapping.rows if row.concept_id}
    controls: list[ControlResult] = []
    for spec in CATALOG:
        if not have or not spec.need.issubset(have):
            status = "not_applicable"
        else:
            status = "insufficient"
        controls.append(ControlResult(id=spec.id, name=spec.name, status=status))
    return FrsDocument(controls=controls, issues=[])
