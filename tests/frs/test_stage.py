from __future__ import annotations

import json
from pathlib import Path

from cashflow_audit.frs.stage import frs_workbook
from cashflow_audit.store.fs import write_json


def test_skip_existing_frs_json(dest: Path) -> None:
    write_json(
        dest / "frs.json",
        {
            "controls": [
                {
                    "id": "F01",
                    "name": "kept",
                    "status": "clear",
                    "confidence": None,
                    "evidence": "",
                    "metrics": {},
                    "cell_refs": [],
                    "issue_id": None,
                }
            ],
            "issues": [],
        },
    )
    doc = frs_workbook(dest)
    assert doc.controls[0].status == "clear"
    assert doc.controls[0].name == "kept"


def test_frs_writes_fourteen_rows_without_prose(dest: Path) -> None:
    write_json(dest / "mapping.json", {"rows": [], "questions": []})
    doc = frs_workbook(dest)
    assert (dest / "frs.json").is_file()
    raw = json.loads((dest / "frs.json").read_text(encoding="utf-8"))
    assert len(raw["controls"]) == 14
    assert "title" not in raw
    assert all(row["status"] == "not_applicable" for row in raw["controls"])
    assert doc.issues == []
