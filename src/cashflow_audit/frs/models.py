from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ControlStatus = Literal["clear", "flagged", "not_applicable", "insufficient"]
Confidence = Literal["high", "medium", "low"]


class ControlResult(BaseModel):
    id: str
    name: str
    status: ControlStatus
    confidence: Confidence | None = None
    evidence: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)
    cell_refs: list[str] = Field(default_factory=list)
    issue_id: str | None = None


class FrsIssue(BaseModel):
    id: str
    control_id: str
    class_name: str = Field(alias="class")
    priority: Literal["high", "medium", "low"]
    metrics: dict[str, Any] = Field(default_factory=dict)
    cell_refs: list[str] = Field(default_factory=list)
    cause: str = ""
    impact: str = ""

    model_config = {"populate_by_name": True}


class FrsDocument(BaseModel):
    controls: list[ControlResult]
    issues: list[FrsIssue] = Field(default_factory=list)
