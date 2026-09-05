from __future__ import annotations

import json
from pathlib import Path

from cashflow_audit.store.fs import write_json


def load_glossary(path: Path) -> dict[tuple[str, str], str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[tuple[str, str], str] = {}
    for entry in data.get("entries", []):
        out[(str(entry["label"]), str(entry.get("parent") or ""))] = str(entry["concept_id"])
    return out


def save_glossary(path: Path, glossary: dict[tuple[str, str], str]) -> None:
    entries = [
        {"label": label, "parent": parent, "concept_id": concept_id}
        for (label, parent), concept_id in sorted(glossary.items())
    ]
    write_json(path, {"entries": entries})
