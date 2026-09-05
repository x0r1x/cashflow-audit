from __future__ import annotations

import asyncio
import json
import shutil
import time

from cashflow_audit.adapters.slots import BusSlotGate
from cashflow_audit.api.context import AppContext
from cashflow_audit.app.pipeline import Pipeline
from cashflow_audit.ports.protocols import Job


async def worker_loop(name: str, ctx: AppContext) -> None:
    while not ctx.stopped:
        try:
            await _cycle(name, ctx)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(0.2)


async def _cycle(name: str, ctx: AppContext) -> None:
    if not await ctx.bus.try_slot("run", name):
        await asyncio.sleep(0.1)
        return
    try:
        job = await ctx.bus.claim(name)
        if job is None:
            await asyncio.sleep(0.05)
            return
        if not await ctx.bus.acquire_audit(job.audit_id):
            await ctx.bus.ack(job)
            return
        try:
            await _execute(job, ctx)
            await ctx.bus.ack(job)
        finally:
            await ctx.bus.release_audit(job.audit_id)
    finally:
        await ctx.bus.release_slot("run", name)


async def _execute(job: Job, ctx: AppContext) -> None:
    dest = ctx.store.dest_dir(job.audit_id)
    owner_path = dest / "owner.json"
    if not owner_path.exists():
        await ctx.bus.set_terminal(job.audit_id, "failed", stage="parse", error="not_found")
        return
    owner = json.loads(owner_path.read_text(encoding="utf-8"))
    await ctx.bus.set_progress(job.audit_id, "parse")
    loop = asyncio.get_running_loop()
    slots = BusSlotGate(ctx.bus, job.audit_id, loop)
    source = dest / "source.xlsx"

    def _run():
        return Pipeline(
            embed=ctx.embed,
            chat=ctx.chat,
            slots=slots,
            glossary_dir=ctx.store.glossary_dir(),
            on_progress=slots.progress,
        ).run(source, dest, actor_id=str(owner.get("actor_id") or "anonymous"))

    hb = asyncio.create_task(_heartbeat(ctx, job.audit_id), name=f"hb-{job.audit_id}")
    try:
        report = await asyncio.to_thread(_run)
    except Exception:
        await ctx.bus.set_terminal(job.audit_id, "failed", stage="parse", error="internal")
        return
    finally:
        hb.cancel()
        try:
            await hb
        except asyncio.CancelledError:
            pass
    stage = "done"
    error = None
    meta_path = dest / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        stage = str(meta.get("stage") or stage)
        error = meta.get("error")
    await ctx.bus.set_terminal(job.audit_id, report.status, stage=stage, error=error)


async def _heartbeat(ctx: AppContext, audit_id: str) -> None:
    interval = max(ctx.heartbeat_sec, 0.01)
    while True:
        await asyncio.sleep(interval)
        await ctx.bus.touch_audit(audit_id)


async def daily_sweep(ctx: AppContext) -> None:
    interval = max(ctx.sweep_interval_sec, 0.01)
    while not ctx.stopped:
        await asyncio.sleep(interval)
        if ctx.stopped:
            break
        await sweep_expired(ctx)


async def reconcile(ctx: AppContext) -> None:
    audits = ctx.store.root / "audits"
    if not audits.is_dir():
        return
    for dest in audits.iterdir():
        if not dest.is_dir():
            continue
        if (dest / "meta.json").exists() or (dest / "report.json").exists():
            continue
        owner_path = dest / "owner.json"
        if not owner_path.exists():
            continue
        owner = json.loads(owner_path.read_text(encoding="utf-8"))
        live = await ctx.bus.get_live(dest.name)
        if live and live.status in {"queued", "running"}:
            continue
        await ctx.bus.mark_queued(dest.name, str(owner.get("actor_id") or ""))
        await ctx.bus.enqueue(dest.name)


async def sweep_expired(ctx: AppContext) -> None:
    audits = ctx.store.root / "audits"
    if not audits.is_dir() or ctx.ttl_days <= 0:
        return
    cutoff = time.time() - ctx.ttl_days * 86400
    for dest in audits.iterdir():
        if not dest.is_dir():
            continue
        live = await ctx.bus.get_live(dest.name)
        if live and live.status == "running":
            continue
        try:
            mtime = dest.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            shutil.rmtree(dest, ignore_errors=True)
