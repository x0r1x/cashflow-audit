from __future__ import annotations

import json
from pathlib import Path

from cashflow_audit.checkers import check_workbook
from cashflow_audit.checkers.models import CheckDocument
from cashflow_audit.store.fs import write_json


def test_skip_existing_candidates_json(dest: Path) -> None:
    payload = {
        "candidates": [
            {
                "detector": "excel_error",
                "cell_refs": ["S!A1"],
                "tags": [],
                "payload": {},
                "base_severity": "error",
            }
        ],
        "questions": [],
    }
    write_json(dest / "candidates.json", payload)
    doc = check_workbook(dest)
    assert isinstance(doc, CheckDocument)
    assert doc.candidates[0].detector == "excel_error"


def test_check_writes_candidates_without_prose(dest: Path) -> None:
    from cashflow_audit.compile import compile_workbook
    from cashflow_audit.layout import layout_workbook
    from cashflow_audit.parse import parse_workbook
    from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

    source = dest.parent / "err.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="S",
                cells=[
                    CellSpec(addr="A1", value="Item", type="s"),
                    CellSpec(addr="B1", value="2023", type="s"),
                    CellSpec(addr="C1", value="2024E", type="s"),
                    CellSpec(addr="A2", value="Line", type="s"),
                    CellSpec(addr="B2", value="#REF!", type="e", formula="Z9"),
                    CellSpec(addr="C2", value="1"),
                ],
            )
        ],
        shared_strings=["Item", "2023", "2024E", "Line"],
    )
    parse_workbook(source, dest)
    compile_workbook(dest)
    layout_workbook(dest)
    write_json(
        dest / "mapping.json",
        {
            "rows": [
                {
                    "row_key": "S|2|S!r1",
                    "sheet": "S",
                    "row": 2,
                    "block_id": "S!r1",
                    "label": "Line",
                    "parent_label": None,
                    "concept_id": None,
                    "article_role": "calculation",
                    "source": "question",
                }
            ],
            "questions": [],
        },
    )
    doc = check_workbook(dest)
    assert (dest / "candidates.json").is_file()
    raw = json.loads((dest / "candidates.json").read_text(encoding="utf-8"))
    assert "title" not in raw
    dumped = doc.candidates[0].model_dump()
    assert "title" not in dumped
    assert "evidence" not in dumped
    assert any(c.detector == "excel_error" for c in doc.candidates)
