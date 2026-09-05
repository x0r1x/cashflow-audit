from __future__ import annotations

import json
from pathlib import Path

from cashflow_audit.lineage import lineage_workbook
from cashflow_audit.store.fs import write_json


def test_skip_existing_lineage_json(dest: Path) -> None:
    payload = {
        "items": [
            {
                "candidate_index": 0,
                "detector": "excel_error",
                "cell_refs": ["S!A1"],
                "output_refs": ["S!B1"],
                "affected_metrics": ["pnl.ebitda"],
                "impact": {"kind": "direction", "value": "downstream"},
                "truncated": False,
                "complete": True,
                "path_refs": ["S!A1", "S!B1"],
            }
        ]
    }
    write_json(dest / "lineage.json", payload)
    doc = lineage_workbook(dest)
    assert doc.items[0].affected_metrics == ["pnl.ebitda"]


def test_lineage_writes_json(dest: Path) -> None:
    from cashflow_audit.compile import compile_workbook
    from cashflow_audit.layout import layout_workbook
    from cashflow_audit.parse import parse_workbook
    from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

    source = dest.parent / "lin.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="Item", type="s"),
                    CellSpec(addr="B1", value="2023", type="s"),
                    CellSpec(addr="C1", value="2024E", type="s"),
                    CellSpec(addr="A2", value="Input", type="s"),
                    CellSpec(addr="B2", value="1"),
                    CellSpec(addr="A3", value="EBITDA", type="s"),
                    CellSpec(addr="B3", value="1", formula="B2"),
                ],
            )
        ],
        shared_strings=["Item", "2023", "2024E", "Input", "EBITDA"],
    )
    parse_workbook(source, dest)
    compile_workbook(dest)
    layout_workbook(dest)
    write_json(
        dest / "mapping.json",
        {
            "rows": [
                {
                    "row_key": "P&L|3|P&L!r1",
                    "sheet": "P&L",
                    "row": 3,
                    "block_id": "P&L!r1",
                    "label": "EBITDA",
                    "parent_label": None,
                    "concept_id": "pnl.ebitda",
                    "article_role": "output",
                    "source": "glossary",
                }
            ],
            "questions": [],
        },
    )
    write_json(
        dest / "candidates.json",
        {
            "candidates": [
                {
                    "detector": "excel_error",
                    "cell_refs": ["P&L!B2"],
                    "tags": [],
                    "payload": {},
                    "base_severity": "error",
                }
            ],
            "questions": [],
        },
    )
    doc = lineage_workbook(dest)
    assert (dest / "lineage.json").is_file()
    data = json.loads((dest / "lineage.json").read_text(encoding="utf-8"))
    assert data["items"][0]["affected_metrics"] == ["pnl.ebitda"]
    assert doc.items[0].complete is True
