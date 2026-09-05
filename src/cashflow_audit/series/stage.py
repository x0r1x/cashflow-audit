from __future__ import annotations

import json
from pathlib import Path

from cashflow_audit.ir.catalog import IrCatalog
from cashflow_audit.layout.models import Layout
from cashflow_audit.series.analyze import analyze_series
from cashflow_audit.series.models import SeriesOutlier
from cashflow_audit.store.fs import read_parquet


def series_workbook(dest_dir: Path, catalog: IrCatalog) -> list[SeriesOutlier]:
    layout = Layout.model_validate(
        json.loads((dest_dir / "layout.json").read_text(encoding="utf-8"))
    )
    cells = read_parquet(dest_dir / "ir" / "cells.parquet")
    outliers = analyze_series(layout, cells)
    catalog.upsert_series_outliers([o.model_dump(mode="json") for o in outliers])
    return outliers
