import asyncio
import logging
import os
from pathlib import Path

from tests.helpers.jobbus import MemoryJobBus

from cashflow_audit.adapters.slots import BusSlotGate
from cashflow_audit.api.context import AppContext
from cashflow_audit.api.workers import _execute, reconcile, sweep_expired
from cashflow_audit.ports.protocols import Job
from cashflow_audit.store.disk import DiskStore
from cashflow_audit.store.fs import write_json


def test_execute_without_owner_logs_job_fail(tmp_path: Path, caplog) -> None:
    bus = MemoryJobBus()
    ctx = AppContext(store=DiskStore(tmp_path / "data"), bus=bus)
    caplog.set_level(logging.INFO, logger="cashflow_audit")
    asyncio.run(_execute(Job(audit_id="deadbeef"), ctx))
    events = [r.__dict__.get("event") for r in caplog.records]
    assert "job_fail" in events
    assert any(r.__dict__.get("error_code") == "not_found" for r in caplog.records)


def test_execute_pipeline_exception_logs_internal(
    tmp_path: Path, caplog, monkeypatch
) -> None:
    bus = MemoryJobBus()
    ctx = AppContext(store=DiskStore(tmp_path / "data"), bus=bus)
    dest = ctx.store.dest_dir("deadbeef")
    dest.mkdir(parents=True)
    write_json(dest / "owner.json", {"actor_id": "u1"})

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("cashflow_audit.api.workers.Pipeline.run", _boom)
    caplog.set_level(logging.INFO, logger="cashflow_audit")
    asyncio.run(_execute(Job(audit_id="deadbeef"), ctx))
    assert any(
        r.__dict__.get("event") == "job_fail"
        and r.__dict__.get("error_code") == "internal"
        and r.__dict__.get("exc_type") == "RuntimeError"
        for r in caplog.records
    )
    live = asyncio.run(bus.get_live("deadbeef"))
    assert live is not None
    assert live.error == "internal"
    assert live.status == "failed"
    assert live.stage == "parse"


def test_execute_pipeline_exception_prefers_meta_stage(
    tmp_path: Path, caplog, monkeypatch
) -> None:
    bus = MemoryJobBus()
    ctx = AppContext(store=DiskStore(tmp_path / "data"), bus=bus)
    dest = ctx.store.dest_dir("deadbeef")
    dest.mkdir(parents=True)
    write_json(dest / "owner.json", {"actor_id": "u1"})
    write_json(dest / "meta.json", {"status": "failed", "stage": "layout", "error": "internal"})

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("cashflow_audit.api.workers.Pipeline.run", _boom)
    caplog.set_level(logging.INFO, logger="cashflow_audit")
    asyncio.run(_execute(Job(audit_id="deadbeef"), ctx))
    live = asyncio.run(bus.get_live("deadbeef"))
    assert live is not None
    assert live.status == "failed"
    assert live.error == "internal"
    assert live.stage == "layout"


def test_wait_slot_timeout_logs(caplog) -> None:
    bus = MemoryJobBus(max_llm=0)
    caplog.set_level(logging.INFO, logger="cashflow_audit")

    async def _go() -> None:
        loop = asyncio.get_running_loop()
        gate = BusSlotGate(bus, "deadbeef", loop)
        assert await gate._wait_slot("llm", 0) is False

    asyncio.run(_go())
    assert any(
        r.__dict__.get("event") == "slot_timeout" and r.__dict__.get("kind") == "llm"
        for r in caplog.records
    )


def test_reconcile_logs_count_not_paths(tmp_path: Path, caplog) -> None:
    bus = MemoryJobBus()
    ctx = AppContext(store=DiskStore(tmp_path / "data"), bus=bus)
    dest = ctx.store.dest_dir("pending1")
    dest.mkdir(parents=True)
    write_json(dest / "owner.json", {"actor_id": "u1"})
    caplog.set_level(logging.INFO, logger="cashflow_audit")
    asyncio.run(reconcile(ctx))
    assert any(
        r.__dict__.get("event") == "reconcile_requeue" and r.__dict__.get("count") == 1
        for r in caplog.records
    )
    assert "pending1" not in caplog.text


def test_sweep_logs_deleted_count_not_paths(tmp_path: Path, caplog) -> None:
    bus = MemoryJobBus()
    ctx = AppContext(store=DiskStore(tmp_path / "data"), bus=bus, ttl_days=1)
    old = ctx.store.dest_dir("oldterm")
    old.mkdir(parents=True)
    write_json(old / "meta.json", {"status": "succeeded", "stage": "done"})
    os.utime(old, (0, 0))
    caplog.set_level(logging.INFO, logger="cashflow_audit")
    asyncio.run(sweep_expired(ctx))
    assert any(
        r.__dict__.get("event") == "sweep_deleted" and r.__dict__.get("count") == 1
        for r in caplog.records
    )
    assert "oldterm" not in caplog.text
    assert not old.exists()
