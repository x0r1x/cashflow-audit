from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Impact(BaseModel):
    kind: Literal["numeric", "direction"]
    value: float | str


class LineageItem(BaseModel):
    candidate_index: int
    detector: str
    cell_refs: list[str]
    output_refs: list[str] = Field(default_factory=list)
    affected_metrics: list[str] = Field(default_factory=list)
    impact: Impact
    truncated: bool = False
    complete: bool = True
    path_refs: list[str] = Field(default_factory=list)


class LineageDocument(BaseModel):
    items: list[LineageItem] = Field(default_factory=list)
