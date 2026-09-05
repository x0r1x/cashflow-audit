from __future__ import annotations

from dataclasses import dataclass, field

from cashflow_audit.compile.models import CsrGraph, Edge
from cashflow_audit.layout.models import Layout
from cashflow_audit.mapping.models import MappingDocument
from cashflow_audit.series.models import SeriesOutlier


@dataclass
class CheckContext:
    cells: list[dict]
    edges: list[Edge]
    workbook: dict
    layout: Layout
    mapping: MappingDocument
    series_outliers: list[SeriesOutlier]
    csr: CsrGraph
    extra: dict = field(default_factory=dict)
