from __future__ import annotations

import asyncio
import os
from pathlib import Path

from tests.api.test_http import api_client
from tests.helpers.jobbus import MemoryJobBus

from cashflow_audit.api.context import AppContext
from cashflow_audit.api.workers import _heartbeat, sweep_expired
from cashflow_audit.ports.protocols import JobState
from cashflow_audit.store.disk import DiskStore
from cashflow_audit.store.fs import write_json


def test_sweep_skips_running_and_deletes_old_terminal(tmp_path: Path) -> None:
    bus = MemoryJobBus()
    ctx = AppContext(store=DiskStore(tmp_path / "data"), bus=bus, ttl_days=1)
    old = ctx.store.dest_dir("oldterm")
    old.mkdir(parents=True)
    write_json(old / "meta.json", {"status": "succeeded", "stage": "done"})
    os.utime(old, (0, 0))
    running = ctx.store.dest_dir("running")
    running.mkdir(parents=True)
    os.utime(running, (0, 0))
    bus.live["running"] = JobState(status="running", stage="parse", actor_id="u1")
    asyncio.run(sweep_expired(ctx))
    assert not old.exists()
    assert running.exists()


def test_heartbeat_touches_audit(tmp_path: Path) -> None:
    bus = MemoryJobBus()
    ctx = AppContext(store=DiskStore(tmp_path / "data"), bus=bus, heartbeat_sec=0.05)

    async def _run() -> None:
        task = asyncio.create_task(_heartbeat(ctx, "a1"))
        await asyncio.sleep(0.16)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())
    assert bus.touches


def test_readyz_probe_down_is_degraded(tmp_path: Path) -> None:
    async def down() -> bool:
        return False

    with api_client(tmp_path, llm_probe=down) as (client, _data, _bus):
        res = client.get("/readyz")
        assert res.status_code == 200
        assert res.json()["status"] == "degraded"
        assert res.json()["llm"] is False
        assert res.json()["redis"] is True
