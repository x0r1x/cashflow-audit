from __future__ import annotations

import json
from pathlib import Path

from cashflow_audit.explain import explain_workbook
from cashflow_audit.store.fs import write_json
from tests.helpers.ports import FakeChat, GrantSlots


def test_skip_existing_report_does_not_call_llm(dest: Path) -> None:
    write_json(
        dest / "report.json",
        {
            "audit_id": dest.name,
            "source_filename": "x.xlsx",
            "sha256": "ab",
            "status": "succeeded",
            "llm_used": False,
            "embeddings_used": False,
            "summary": {
                "findings": 0,
                "by_severity": {"error": 0, "warning": 0, "risk": 0},
                "questions": 0,
            },
            "findings": [],
            "questions": [],
            "provenance": {"detector_version": "1", "llm_model": None, "embedding_model": None},
        },
    )
    write_json(dest / "meta.json", {"status": "succeeded", "stage": "done", "error": None})
    write_json(dest / "integrity.json", {"findings": []})
    chat = FakeChat()
    explain_workbook(dest, chat=chat, slots=GrantSlots())
    assert chat.calls == 0


def test_writes_report_and_meta_atomically(dest: Path) -> None:
    write_json(
        dest / "owner.json",
        {"actor_id": "a", "content_sha256": "deadbeef", "source_filename": "m.xlsx"},
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
    write_json(
        dest / "lineage.json",
        {
            "items": [
                {
                    "candidate_index": 0,
                    "detector": "excel_error",
                    "cell_refs": ["P&L!B2"],
                    "output_refs": ["P&L!C3"],
                    "affected_metrics": ["pnl.ebitda"],
                    "impact": {"kind": "direction", "value": "downstream"},
                    "truncated": False,
                    "complete": True,
                    "path_refs": ["P&L!B2", "P&L!C3"],
                }
            ]
        },
    )
    write_json(dest / "mapping.json", {"rows": [], "questions": []})
    (dest / "ir").mkdir(exist_ok=True)
    from cashflow_audit.store.fs import write_parquet

    write_parquet(
        dest / "ir" / "cells.parquet",
        [
            ("sheet", "VARCHAR"),
            ("row", "INTEGER"),
            ("col", "INTEGER"),
            ("addr", "VARCHAR"),
            ("formula_raw", "VARCHAR"),
            ("formula_template", "VARCHAR"),
            ("unparsed", "BOOLEAN"),
            ("cached_value", "VARCHAR"),
            ("hidden", "BOOLEAN"),
            ("number_format", "VARCHAR"),
            ("comment", "VARCHAR"),
            ("ast_json", "VARCHAR"),
        ],
        [("P&L", 2, 2, "B2", None, None, False, "1", False, None, None, None)],
    )
    report = explain_workbook(dest, chat=None, slots=GrantSlots())
    assert (dest / "report.json").is_file()
    assert (dest / "integrity.json").is_file()
    assert (dest / "meta.json").is_file()
    meta = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
    assert meta["stage"] == "done"
    assert report.findings
    assert report.sha256 == "deadbeef"
    assert report.source_filename == "m.xlsx"
