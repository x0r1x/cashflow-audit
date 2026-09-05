from __future__ import annotations

from pathlib import Path


class DiskStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def dest_dir(self, audit_id: str) -> Path:
        return self.root / "audits" / audit_id

    def glossary_dir(self) -> Path:
        return self.root / "glossary"
