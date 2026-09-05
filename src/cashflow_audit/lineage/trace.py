from __future__ import annotations

from cashflow_audit.checkers.models import Candidate
from cashflow_audit.compile.models import CsrGraph
from cashflow_audit.graph.reach import reverse_reachable_from
from cashflow_audit.lineage.models import Impact, LineageDocument, LineageItem
from cashflow_audit.mapping.models import MappingDocument
from cashflow_audit.parse.a1 import parse_addr


def trace_lineage(
    candidates: list[Candidate],
    *,
    csr: CsrGraph,
    mapping: MappingDocument,
) -> LineageDocument:
    by_row = _concept_index(mapping)
    items: list[LineageItem] = []
    for index, cand in enumerate(candidates):
        path = reverse_reachable_from(csr, cand.cell_refs)
        outputs, metrics = _outputs_on(path, by_row)
        truncated = bool(path & csr.truncated_sources)
        items.append(
            LineageItem(
                candidate_index=index,
                detector=cand.detector,
                cell_refs=list(cand.cell_refs),
                output_refs=sorted(outputs),
                affected_metrics=sorted(metrics),
                impact=_impact(cand),
                truncated=truncated,
                complete=not truncated,
                path_refs=sorted(path),
            )
        )
    return LineageDocument(items=items)


def _concept_index(
    mapping: MappingDocument,
) -> dict[tuple[str, int], tuple[str, str]]:
    out: dict[tuple[str, int], tuple[str, str]] = {}
    for row in mapping.rows:
        if not row.concept_id:
            continue
        out[(row.sheet, row.row)] = (row.concept_id, row.article_role)
    return out


def _outputs_on(
    path: set[str],
    by_row: dict[tuple[str, int], tuple[str, str]],
) -> tuple[list[str], list[str]]:
    outputs: list[str] = []
    metrics: list[str] = []
    for ref in path:
        key = _sheet_row(ref)
        if key is None or key not in by_row:
            continue
        concept_id, role = by_row[key]
        if role != "output":
            continue
        outputs.append(ref)
        if concept_id not in metrics:
            metrics.append(concept_id)
    return outputs, metrics


def _sheet_row(ref: str) -> tuple[str, int] | None:
    sheet, sep, addr = ref.partition("!")
    if not sep:
        return None
    try:
        _col, row = parse_addr(addr)
    except ValueError:
        return None
    return sheet, row


def _impact(cand: Candidate) -> Impact:
    delta = cand.payload.get("delta")
    if isinstance(delta, int | float) and not isinstance(delta, bool):
        return Impact(kind="numeric", value=float(delta))
    return Impact(kind="direction", value="downstream")
