from __future__ import annotations

import asyncio
import logging
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


def test_readyz_settings_probe_logs_once_per_change(
    tmp_path: Path, caplog, monkeypatch
) -> None:
    from cashflow_audit.api.probes import _LAST_PROBE, ProbeResult, llm_probe_from_settings
    from cashflow_audit.api.routes import _LAST_PORT_STATE
    from cashflow_audit.settings import Settings

    async def fake_probe(*_args: object, **kwargs: object) -> ProbeResult:
        from cashflow_audit.api.probes import _emit

        return _emit(
            ProbeResult(
                configured=True, reachable=False, model_present=None, error="http_401"
            ),
            msg="models",
            port=str(kwargs.get("port") or "llm"),
            model="qwen",
            http_status=401,
            reason="http_401",
        )

    monkeypatch.setenv("LLM_BASE_URL", "http://llm/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "qwen")
    monkeypatch.setattr("cashflow_audit.api.probes.probe_models", fake_probe)
    _LAST_PROBE.clear()
    _LAST_PORT_STATE.clear()
    settings = Settings(_env_file=None)
    caplog.set_level(logging.INFO, logger="cashflow_audit")
    with api_client(tmp_path, llm_probe=llm_probe_from_settings(settings)) as (
        client,
        _data,
        _bus,
    ):
        caplog.clear()
        res = client.get("/readyz")
        assert res.status_code == 200
        records = [
            r
            for r in caplog.records
            if r.__dict__.get("event") == "probe_models" and r.__dict__.get("port") == "llm"
        ]
        assert len(records) == 1
        caplog.clear()
        client.get("/readyz")
        assert not any(
            r.__dict__.get("event") == "probe_models" and r.__dict__.get("port") == "llm"
            for r in caplog.records
        )


def test_readyz_probe_info_only_on_change(tmp_path: Path, caplog) -> None:
    from cashflow_audit.api.routes import _LAST_PORT_STATE

    states = iter((True, True, False))

    async def flip() -> bool:
        return next(states)

    _LAST_PORT_STATE.clear()
    caplog.set_level(logging.INFO, logger="cashflow_audit")
    with api_client(tmp_path, llm_probe=flip) as (client, _data, _bus):
        caplog.clear()
        first = client.get("/readyz")
        assert first.status_code == 200
        assert any(
            r.__dict__.get("event") == "probe_models" and r.__dict__.get("port") == "llm"
            for r in caplog.records
        )
        caplog.clear()
        client.get("/readyz")
        assert not any(
            r.__dict__.get("event") == "probe_models" and r.__dict__.get("port") == "llm"
            for r in caplog.records
        )
        caplog.clear()
        third = client.get("/readyz")
        assert third.json()["status"] == "degraded"
        assert any(
            r.__dict__.get("event") == "probe_models" and r.__dict__.get("port") == "llm"
            for r in caplog.records
        )
