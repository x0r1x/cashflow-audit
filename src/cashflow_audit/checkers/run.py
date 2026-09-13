from __future__ import annotations

from cashflow_audit.checkers.context import CheckContext
from cashflow_audit.checkers.detectors import (
    detect_agg_double_count,
    detect_agg_range_gap,
    detect_circular,
    detect_error_masking,
    detect_excel_error,
    detect_external_link,
    detect_hidden_input,
    detect_scenario_switch,
    detect_series,
    detect_stress_rate_unchanged,
    detect_unresolved_dynamic,
    detect_unused_cell,
    detect_xlm_or_vba,
)
from cashflow_audit.checkers.identity import detect_identities
from cashflow_audit.checkers.models import Candidate, CheckDocument

FREEZE_TAGS = {"likely_intentional", "edge_period", "hist_manual_adjustment"}


def run_checks(ctx: CheckContext) -> CheckDocument:
    candidates: list[Candidate] = []
    candidates.extend(detect_excel_error(ctx))
    candidates.extend(detect_error_masking(ctx))
    candidates.extend(detect_circular(ctx))
    candidates.extend(detect_unresolved_dynamic(ctx))
    candidates.extend(detect_external_link(ctx))
    candidates.extend(detect_xlm_or_vba(ctx))
    candidates.extend(detect_series(ctx))
    candidates.extend(detect_agg_range_gap(ctx))
    candidates.extend(detect_agg_double_count(ctx))
    candidates.extend(detect_unused_cell(ctx))
    candidates.extend(detect_hidden_input(ctx))
    candidates.extend(detect_scenario_switch(ctx))
    candidates.extend(detect_stress_rate_unchanged(ctx))
    identity, questions = detect_identities(ctx)
    candidates.extend(identity)
    candidates = _dedup(candidates)
    for cand in candidates:
        if FREEZE_TAGS.intersection(cand.tags) and cand.base_severity == "error":
            cand.base_severity = "warning"
    return CheckDocument(candidates=candidates, questions=questions)


def _dedup(candidates: list[Candidate]) -> list[Candidate]:
    seen: set[tuple[str, frozenset[str]]] = set()
    out: list[Candidate] = []
    for cand in candidates:
        key = (cand.detector, frozenset(cand.cell_refs))
        if key in seen:
            continue
        seen.add(key)
        out.append(cand)
    return out
