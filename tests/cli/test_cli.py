from __future__ import annotations

import json
from pathlib import Path

from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx
from typer.testing import CliRunner

from cashflow_audit.app.ids import audit_id_for, sha256_bytes


def _book(path: Path) -> Path:
    return build_xlsx(
        path,
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


def test_cli_audit_writes_report(tmp_path: Path) -> None:
    from cashflow_audit.cli import app

    source = _book(tmp_path / "m.xlsx")
    output = tmp_path / "out" / "report.json"
    data_dir = tmp_path / "data"
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["audit", str(source), "-o", str(output), "--data-dir", str(data_dir)],
    )
    assert result.exit_code == 0, result.output
    assert output.is_file()
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] in {"succeeded", "needs_input", "degraded"}
    audit_id = audit_id_for("anonymous", sha256_bytes(source.read_bytes()))
    owner = json.loads((data_dir / "audits" / audit_id / "owner.json").read_text())
    assert owner["actor_id"] == "anonymous"


def test_cli_rejects_bad_extension(tmp_path: Path) -> None:
    from cashflow_audit.cli import app

    source = tmp_path / "m.xls"
    source.write_bytes(b"not-xlsx")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "audit",
            str(source),
            "-o",
            str(tmp_path / "r.json"),
            "--data-dir",
            str(tmp_path / "data"),
        ],
    )
    assert result.exit_code != 0
