from __future__ import annotations

from pathlib import Path

from cashflow_audit.compile import compile_workbook
from cashflow_audit.ir.catalog import IrCatalog
from cashflow_audit.layout import layout_workbook
from cashflow_audit.parse import parse_workbook
from cashflow_audit.series import series_workbook
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx


def test_series_creates_view_not_file_and_checker_selects_kind(
    tmp_path: Path, dest: Path
) -> None:
    source = tmp_path / "series.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="Item", type="s"),
                    CellSpec(addr="B1", value="2023", type="s"),
                    CellSpec(addr="C1", value="2024E", type="s"),
                    CellSpec(addr="D1", value="2025E", type="s"),
                    CellSpec(addr="E1", value="2026E", type="s"),
                    CellSpec(addr="A2", value="Revenue", type="s"),
                    CellSpec(addr="B2", value="10", formula="A2"),
                    CellSpec(addr="C2", value="11", formula="B2"),
                    CellSpec(addr="D2", value="12", formula="C2"),
                    CellSpec(addr="E2", value="1500"),
                ],
            )
        ],
        shared_strings=["Item", "2023", "2024E", "2025E", "2026E", "Revenue"],
    )
    parse_workbook(source, dest)
    compile_workbook(dest)
    catalog = IrCatalog.open(dest)
    try:
        layout_workbook(dest, catalog)
        series_workbook(dest, catalog)
        assert not (dest / "series.json").exists()
        assert not list(dest.glob("**/series*.json"))
        kinds = catalog.sql(
            "SELECT kind FROM series_outliers WHERE kind = 'value_instead_of_formula'"
        ).fetchall()
        assert kinds == [("value_instead_of_formula",)]
    finally:
        catalog.close()
