from __future__ import annotations

from pathlib import Path
from typing import Any

from cashflow_audit.mapping.models import MappingDocument
from cashflow_audit.mapping.normalize import normalize_label
from cashflow_audit.parse.a1 import parse_addr
from cashflow_audit.store.glossary import load_glossary, save_glossary

_TAIL = (
    "mapping.json",
    "candidates.json",
    "lineage.json",
    "frs.json",
    "integrity.json",
    "report.json",
    "meta.json",
)


def apply_hitl(
    dest_dir: Path,
    answers: list[dict[str, Any]],
    *,
    actor_id: str,
    glossary_dir: Path,
) -> None:
    mapping_path = dest_dir / "mapping.json"
    if mapping_path.exists() and answers:
        mapping = MappingDocument.model_validate_json(mapping_path.read_text(encoding="utf-8"))
        _merge_answers(mapping, answers, glossary_dir / f"{actor_id}.json")
    for name in _TAIL:
        path = dest_dir / name
        if path.exists():
            path.unlink()


def _merge_answers(
    mapping: MappingDocument,
    answers: list[dict[str, Any]],
    glossary_path: Path,
) -> None:
    glossary = load_glossary(glossary_path)
    by_id = {q.id: q for q in mapping.questions}
    rows = {(row.sheet, row.row): row for row in mapping.rows}
    for answer in answers:
        question = by_id.get(str(answer.get("question_id", "")))
        concept_id = answer.get("concept_id")
        if question is None or not concept_id or not question.cell_refs:
            continue
        key = _sheet_row(question.cell_refs[0])
        if key is None or key not in rows:
            continue
        mapped = rows[key]
        glossary[
            (normalize_label(mapped.label), normalize_label(mapped.parent_label))
        ] = str(concept_id)
    save_glossary(glossary_path, glossary)


def _sheet_row(ref: str) -> tuple[str, int] | None:
    sheet, sep, addr = ref.partition("!")
    if not sep:
        return None
    try:
        _col, row = parse_addr(addr)
    except ValueError:
        return None
    return sheet, row
