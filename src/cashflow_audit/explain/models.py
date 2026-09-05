from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from cashflow_audit.mapping.models import MappingQuestion

Severity = Literal["error", "warning", "risk"]
ReportStatus = Literal["succeeded", "needs_input", "degraded", "failed"]


class ExplainCard(BaseModel):
    title: str
    evidence: str
    recommendation: str
    need_user_input: bool = False
    cited_refs: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    id: str
    severity: Severity
    detector: str
    cell_refs: list[str]
    title: str
    evidence: str
    affected_metrics: list[str] = Field(default_factory=list)
    impact: str
    recommendation: str
    related_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    need_user_input: bool = False


class ReportSummary(BaseModel):
    findings: int
    by_severity: dict[str, int]
    questions: int


class Provenance(BaseModel):
    detector_version: str = "1"
    llm_model: str | None = None
    embedding_model: str | None = None


class Report(BaseModel):
    audit_id: str
    source_filename: str
    sha256: str
    status: ReportStatus
    llm_used: bool
    embeddings_used: bool
    summary: ReportSummary
    findings: list[Finding]
    questions: list[MappingQuestion] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)


class JobMeta(BaseModel):
    status: ReportStatus
    stage: str = "done"
    error: str | None = None
