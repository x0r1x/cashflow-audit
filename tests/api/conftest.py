from __future__ import annotations

from pathlib import Path

import pytest
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

XLSX_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def book(tmp_path: Path) -> Path:
    return build_xlsx(
        tmp_path / "m.xlsx",
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="Item", type="s"),
                    CellSpec(addr="B1", value="2023", type="s"),
                    CellSpec(addr="C1", value="2024E", type="s"),
                    CellSpec(addr="A2", value="Revenue", type="s"),
                    CellSpec(addr="B2", value="10"),
                    CellSpec(addr="C2", value="11"),
                ],
            )
        ],
        shared_strings=["Item", "2023", "2024E", "Revenue"],
    )
