from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from cashflow_audit.frs.models import ControlResult, FrsIssue
from cashflow_audit.mapping.models import MappingQuestion

Severity = Literal["error", "warning", "risk"]
ReportStatus = Literal["succeeded", "needs_input", "degraded", "failed"]


class ExplainCard(BaseModel):
    title: str
    evidence: str
    recommendation: str
    need_user_input: bool = False
    cited_refs: list[str] = Field(default_factory=list)


class IssueProse(BaseModel):
    cause: str
    impact: str
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


class Conclusion(BaseModel):
    id: str
    kind: Literal["trust", "combo", "dynamics"]
    severity: Severity
    metrics: list[str] = Field(default_factory=list)
    title: str
    body: str
    finding_ids: list[str]
    cell_refs: list[str]
    recommendation: str


class ReportSummary(BaseModel):
    findings: int
    by_severity: dict[str, int]
    questions: int
    headline: str = ""


class Provenance(BaseModel):
    detector_version: str = "1"
    llm_model: str | None = None
    embedding_model: str | None = None


class Verdict(BaseModel):
    integrity: str = ""
    trends: str = ""
    risks: str = ""
    liquidity: str = ""
    recommendation: str = ""
    ready_for_credit: bool | None = None

    @field_validator("ready_for_credit")
    @classmethod
    def _never_true(cls, value: bool | None) -> bool | None:
        if value is True:
            return False
        return value


class Positive(BaseModel):
    control_id: str
    text: str


class ControlExplanation(BaseModel):
    check: str = ""
    status_reason: str = ""
    key_fact: str = ""
    impact: str = ""
    next_step: str = ""
    cited_refs: list[str] = Field(default_factory=list)


class RiskScreenRow(ControlResult):
    explanation: ControlExplanation = Field(default_factory=ControlExplanation)


class RiskScreen(BaseModel):
    matrix: list[RiskScreenRow] = Field(default_factory=list)
    issues: list[FrsIssue] = Field(default_factory=list)
    positives: list[Positive] = Field(default_factory=list)
    verdict: Verdict = Field(default_factory=Verdict)

    def __len__(self) -> int:
        return len(self.matrix)

    def __getitem__(self, index: int) -> RiskScreenRow:
        return self.matrix[index]


class Report(BaseModel):
    audit_id: str
    source_filename: str
    sha256: str
    status: ReportStatus
    llm_used: bool
    embeddings_used: bool
    summary: ReportSummary
    findings: list[Finding]
    conclusions: list[Conclusion] = Field(default_factory=list)
    questions: list[MappingQuestion] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)
    risk_screen: RiskScreen = Field(default_factory=RiskScreen)
    integrity_findings: list[Finding] = Field(default_factory=list, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy_risk_screen(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        screen = data.get("risk_screen")
        if isinstance(screen, list):
            data["risk_screen"] = {
                "matrix": screen,
                "issues": data.pop("issues", []),
                "positives": data.pop("positives", []),
                "verdict": data.pop("verdict", {}),
            }
        return data

    @property
    def issues(self) -> list[FrsIssue]:
        return self.risk_screen.issues

    @property
    def positives(self) -> list[Positive]:
        return self.risk_screen.positives

    @property
    def verdict(self) -> Verdict:
        return self.risk_screen.verdict


class JobMeta(BaseModel):
    status: ReportStatus
    stage: str = "done"
    error: str | None = None
