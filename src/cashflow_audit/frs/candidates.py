from __future__ import annotations

from cashflow_audit.checkers.models import Candidate
from cashflow_audit.frs.models import FrsDocument


def issues_as_candidates(doc: FrsDocument) -> list[Candidate]:
    found: list[Candidate] = []
    for issue in doc.issues:
        found.append(
            Candidate(
                detector=f"frs.{issue.control_id}",
                cell_refs=list(issue.cell_refs),
                payload=dict(issue.metrics),
                base_severity="risk",
            )
        )
    return found
