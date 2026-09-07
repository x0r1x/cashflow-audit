from __future__ import annotations

import inspect
import json
import logging
from pathlib import Path

from cashflow_audit.app.pipeline import Pipeline
from cashflow_audit.errors import PortError
from cashflow_audit.explain.models import Report
from tests.helpers.ports import FakeChat, FakeEmbed, GrantSlots
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx


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


class RecordingSlots(GrantSlots):
    def __init__(self) -> None:
        self.released: list[str] = []

    def release(self, kind: str, *args: object, **kwargs: object) -> None:
        self.released.append(kind)


class BoomEmbed:
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise PortError("embed down")


def test_run_has_no_resume_from() -> None:
    assert "resume_from" not in inspect.signature(Pipeline.run).parameters


def test_skip_second_run_does_not_call_llm(tmp_path: Path, dest: Path) -> None:
    source = _book(tmp_path / "m.xlsx")
    first_chat, first_embed = FakeChat(), FakeEmbed()
    Pipeline(chat=first_chat, embed=first_embed, slots=GrantSlots()).run(source, dest)
    assert (dest / "report.json").is_file()
    second_chat, second_embed = FakeChat(), FakeEmbed()
    Pipeline(chat=second_chat, embed=second_embed, slots=GrantSlots()).run(source, dest)
    assert second_chat.calls == 0
    assert second_embed.calls == 0


def test_second_run_logs_stage_skip(tmp_path: Path, dest: Path, caplog) -> None:
    source = _book(tmp_path / "m.xlsx")
    Pipeline(chat=FakeChat(), embed=FakeEmbed(), slots=GrantSlots()).run(source, dest)
    caplog.set_level(logging.INFO, logger="cashflow_audit")
    Pipeline(chat=FakeChat(), embed=FakeEmbed(), slots=GrantSlots()).run(source, dest)
    events = [r.__dict__.get("event") for r in caplog.records]
    assert "stage_skip" in events
    assert any(
        r.__dict__.get("stage") == "parse" and r.__dict__.get("event") == "stage_skip"
        for r in caplog.records
    )


def test_port_error_does_not_raise(tmp_path: Path, dest: Path, caplog) -> None:
    caplog.set_level(logging.INFO, logger="cashflow_audit")
    source = _book(tmp_path / "m.xlsx")
    report = Pipeline(chat=FakeChat(), embed=BoomEmbed(), slots=GrantSlots()).run(source, dest)
    assert isinstance(report, Report)
    assert report.status in {"succeeded", "needs_input", "degraded"}
    blob = "\n".join(r.getMessage() for r in caplog.records)
    extras = "\n".join(f"{k}={v}" for r in caplog.records for k, v in r.__dict__.items())
    dumped = f"{blob}\n{extras}"
    assert "Revenue" not in dumped
    assert "formula_raw" not in dumped
    assert "=A1" not in dumped
    assert "m.xlsx" not in dumped


def test_timeout_writes_failed_and_releases_run_slot(
    tmp_path: Path, dest: Path, caplog
) -> None:
    caplog.set_level(logging.INFO, logger="cashflow_audit")
    source = _book(tmp_path / "m.xlsx")
    slots = RecordingSlots()
    ticks = iter([0.0, 100.0, 100.0, 100.0])
    report = Pipeline(
        chat=None,
        embed=None,
        slots=slots,
        timeout_sec=1,
        clock=lambda: next(ticks),
    ).run(source, dest)
    assert report.status == "failed"
    meta = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "failed"
    assert meta["error"] == "timeout"
    assert "run" in slots.released
    assert any(
        r.__dict__.get("event") == "pipeline_fail"
        and r.__dict__.get("error_code") == "timeout"
        for r in caplog.records
    )


def test_catalog_closed_after_run(tmp_path: Path, dest: Path) -> None:
    source = _book(tmp_path / "m.xlsx")
    pipe = Pipeline(chat=None, embed=None, slots=GrantSlots())
    pipe.run(source, dest)
    assert pipe.catalog is None or _closed(pipe.catalog)


def _closed(catalog: object) -> bool:
    con = getattr(catalog, "_con", None)
    if con is None:
        return True
    try:
        con.execute("SELECT 1")
    except Exception:
        return True
    return False
