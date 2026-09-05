from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

SeriesKind = Literal[
    "template_change",
    "value_instead_of_formula",
    "literal_in_ast",
    "ref_shift",
    "source_sheet_change",
]


class SeriesOutlier(BaseModel):
    sheet: str
    block_id: str
    row: int
    col: int
    addr: str
    cell_ref: str
    kind: SeriesKind
    edge_period: bool
    majority_template: str | None = None
    cell_template: str | None = None
