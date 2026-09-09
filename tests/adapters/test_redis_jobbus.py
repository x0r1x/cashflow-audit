from __future__ import annotations

import asyncio

import pytest

from cashflow_audit.adapters.redis_jobbus import RedisJobBus


def _bus() -> RedisJobBus:
    fakeredis = pytest.importorskip("fakeredis")
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    return RedisJobBus(client, max_run=1, max_llm_calls=1, max_embed_calls=1)


def test_job_hash_progress_and_terminal() -> None:
    async def _run() -> None:
        bus = _bus()
        await bus.mark_queued("a1", "u1")
        live = await bus.get_live("a1")
        assert live is not None
        assert live.status == "queued"
        assert live.actor_id == "u1"
        await bus.set_progress("a1", "parse")
        live = await bus.get_live("a1")
        assert live is not None
        assert live.status == "running"
        assert live.stage == "parse"
        await bus.set_terminal("a1", "succeeded", stage="done")
        live = await bus.get_live("a1")
        assert live is not None
        assert live.status == "succeeded"

    asyncio.run(_run())


def test_acquire_audit_is_exclusive() -> None:
    async def _run() -> None:
        bus = _bus()
        assert await bus.acquire_audit("a1") is True
        assert await bus.acquire_audit("a1") is False
        await bus.release_audit("a1")
        assert await bus.acquire_audit("a1") is True

    asyncio.run(_run())


def test_mark_queued_does_not_replace_terminal() -> None:
    async def _run() -> None:
        bus = _bus()
        await bus.mark_queued("a1", "u1")
        await bus.set_terminal("a1", "succeeded", stage="done")
        await bus.mark_queued("a1", "u1")
        live = await bus.get_live("a1")
        assert live is not None
        assert live.status == "succeeded"
        assert live.stage == "done"

    asyncio.run(_run())


def test_mark_queued_replace_terminal_requeues() -> None:
    async def _run() -> None:
        bus = _bus()
        await bus.mark_queued("a1", "u1")
        await bus.set_terminal("a1", "needs_input", stage="done")
        await bus.mark_queued("a1", "u1", replace_terminal=True)
        live = await bus.get_live("a1")
        assert live is not None
        assert live.status == "queued"
        assert live.stage == "queued"

    asyncio.run(_run())


def test_mark_queued_does_not_touch_running() -> None:
    async def _run() -> None:
        bus = _bus()
        await bus.mark_queued("a1", "u1")
        await bus.set_progress("a1", "check")
        await bus.mark_queued("a1", "u1", replace_terminal=True)
        live = await bus.get_live("a1")
        assert live is not None
        assert live.status == "running"
        assert live.stage == "check"

    asyncio.run(_run())


def test_mark_queued_replaces_failed() -> None:
    async def _run() -> None:
        bus = _bus()
        await bus.mark_queued("a1", "u1")
        await bus.set_terminal("a1", "failed", stage="parse", error="timeout")
        await bus.mark_queued("a1", "u1")
        live = await bus.get_live("a1")
        assert live is not None
        assert live.status == "queued"
        assert live.error is None

    asyncio.run(_run())


def test_enqueue_pending_is_idempotent() -> None:
    async def _run() -> None:
        bus = _bus()
        await bus.enqueue("a1")
        await bus.enqueue("a1")
        pending = await bus._r.scard("cf:pending")
        assert pending == 1

    asyncio.run(_run())
