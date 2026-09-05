from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from cashflow_audit.mapping.models import MappingQuestion

Severity = Literal["error", "warning", "risk"]


class Candidate(BaseModel):
    detector: str
    cell_refs: list[str]
    tags: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    base_severity: Severity = "warning"


class CheckDocument(BaseModel):
    candidates: list[Candidate] = Field(default_factory=list)
    questions: list[MappingQuestion] = Field(default_factory=list)
