from __future__ import annotations

import json
from pathlib import Path

from cashflow_audit.app.hitl import apply_hitl
from cashflow_audit.store.fs import write_json, write_parquet
from cashflow_audit.store.glossary import load_glossary


def test_hitl_deletes_tail_keeps_owner_and_ir(tmp_path: Path, dest: Path) -> None:
    write_json(
        dest / "owner.json",
        {"actor_id": "u1", "content_sha256": "abc", "source_filename": "m.xlsx"},
    )
    (dest / "raw").mkdir()
    (dest / "ir").mkdir()
    (dest / "raw" / "workbook.json").write_text("{}", encoding="utf-8")
    write_parquet(
        dest / "ir" / "cells.parquet",
        [("sheet", "VARCHAR"), ("row", "INTEGER"), ("col", "INTEGER"), ("addr", "VARCHAR")],
        [("P&L", 2, 1, "A2")],
    )
    write_json(dest / "layout.json", {"sheets": []})
    write_json(
        dest / "mapping.json",
        {
            "rows": [
                {
                    "row_key": "P&L|2|P&L!r1",
                    "sheet": "P&L",
                    "row": 2,
                    "block_id": "P&L!r1",
                    "label": "Revenue",
                    "parent_label": None,
                    "concept_id": None,
                    "article_role": "calculation",
                    "source": "question",
                }
            ],
            "questions": [
                {
                    "id": "q_001",
                    "kind": "mapping",
                    "prompt": "Строка «Revenue» — это pnl.revenue?",
                    "cell_refs": ["P&L!A2"],
                    "options": ["pnl.revenue", "unknown"],
                }
            ],
        },
    )
    write_json(dest / "candidates.json", {"candidates": [], "questions": []})
    write_json(dest / "lineage.json", {"items": []})
    write_json(dest / "report.json", {"audit_id": dest.name, "findings": [], "questions": []})
    write_json(dest / "integrity.json", {"findings": []})
    write_json(dest / "frs.json", {"controls": []})
    write_json(dest / "meta.json", {"status": "needs_input", "stage": "done", "error": None})

    glossary_dir = tmp_path / "glossary"
    apply_hitl(
        dest,
        answers=[{"question_id": "q_001", "concept_id": "pnl.revenue"}],
        actor_id="u1",
        glossary_dir=glossary_dir,
    )

    assert (dest / "owner.json").is_file()
    assert json.loads((dest / "owner.json").read_text())["actor_id"] == "u1"
    assert (dest / "ir" / "cells.parquet").is_file()
    assert (dest / "raw" / "workbook.json").is_file()
    assert (dest / "layout.json").is_file()
    assert not (dest / "mapping.json").exists()
    assert not (dest / "candidates.json").exists()
    assert not (dest / "lineage.json").exists()
    assert not (dest / "frs.json").exists()
    assert not (dest / "integrity.json").exists()
    assert not (dest / "report.json").exists()
    assert not (dest / "meta.json").exists()
    loaded = load_glossary(glossary_dir / "u1.json")
    assert loaded[("revenue", "")] == "pnl.revenue"


def test_hitl_has_no_resume_from() -> None:
    import inspect

    from cashflow_audit.app.hitl import apply_hitl

    assert "resume_from" not in inspect.signature(apply_hitl).parameters
