from pathlib import Path

import pytest


@pytest.fixture
def dest(tmp_path: Path) -> Path:
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    return audit_dir
