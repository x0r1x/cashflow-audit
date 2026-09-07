from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from cashflow_audit.api.context import MAX_UPLOAD_BYTES, AppContext
from cashflow_audit.api.errors import ApiError, api_error_handler, audit_error_handler
from cashflow_audit.api.probes import (
    embed_probe_from_settings,
    llm_probe_from_settings,
    log_startup_ports,
)
from cashflow_audit.api.routes import router
from cashflow_audit.api.workers import daily_sweep, reconcile, sweep_expired, worker_loop
from cashflow_audit.errors import AuditError
from cashflow_audit.observability import configure_logging
from cashflow_audit.ports.protocols import ChatPort, EmbedPort, JobBus
from cashflow_audit.settings import Settings
from cashflow_audit.store.disk import DiskStore


def create_app(
    *,
    data_root: Path,
    bus: JobBus,
    chat: ChatPort | None = None,
    embed: EmbedPort | None = None,
    run_workers: bool = False,
    worker_concurrency: int | None = None,
    max_upload_bytes: int = MAX_UPLOAD_BYTES,
    llm_ok: bool | None = None,
    embed_ok: bool | None = None,
    llm_probe: Callable[[], Awaitable[bool | None]] | None = None,
    embed_probe: Callable[[], Awaitable[bool | None]] | None = None,
    ttl_days: int | None = None,
    heartbeat_sec: float = 30.0,
    sweep_interval_sec: float = 86400.0,
    settings: Settings | None = None,
) -> FastAPI:
    if worker_concurrency is None:
        worker_concurrency = settings.worker_concurrency if settings is not None else 4
    if ttl_days is None:
        ttl_days = settings.audit_ttl_days if settings is not None else 14
    ctx = AppContext(
        store=DiskStore(data_root),
        bus=bus,
        chat=chat,
        embed=embed,
        run_workers=run_workers,
        worker_concurrency=worker_concurrency,
        max_upload_bytes=max_upload_bytes,
        llm_ok=llm_ok,
        embed_ok=embed_ok,
        llm_probe=llm_probe,
        embed_probe=embed_probe,
        ttl_days=ttl_days,
        heartbeat_sec=heartbeat_sec,
        sweep_interval_sec=sweep_interval_sec,
        settings=settings,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.ctx = ctx
        if ctx.settings is not None:
            configure_logging(level=ctx.settings.log_level, json_output=ctx.settings.log_json)
            await log_startup_ports(ctx)
        if ctx.run_workers:
            await sweep_expired(ctx)
            await reconcile(ctx)
            ctx.worker_tasks = [
                asyncio.create_task(worker_loop(f"worker-{i}", ctx), name=f"worker-{i}")
                for i in range(ctx.worker_concurrency)
            ]
            ctx.worker_tasks.append(asyncio.create_task(daily_sweep(ctx), name="daily-sweep"))
        yield
        ctx.stopped = True
        for task in ctx.worker_tasks:
            task.cancel()
        if ctx.worker_tasks:
            await asyncio.gather(*ctx.worker_tasks, return_exceptions=True)

    app = FastAPI(title="cashflow-audit", lifespan=lifespan)
    app.state.ctx = ctx
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(AuditError, audit_error_handler)
    app.include_router(router)
    return app


def app_from_env(*, data_dir: Path | None = None) -> FastAPI:
    from cashflow_audit.adapters.redis_jobbus import RedisJobBus

    settings = Settings()
    root = data_dir or settings.data_dir
    bus = RedisJobBus.from_url(
        settings.require_redis(),
        max_run=settings.inflight,
        max_llm=settings.max_llm_inflight,
        max_embed=settings.max_embed_inflight,
        max_llm_calls=settings.job_llm_budget,
        max_embed_calls=settings.job_embed_budget,
    )
    return create_app(
        data_root=root,
        bus=bus,
        chat=settings.chat(),
        embed=settings.embed(),
        run_workers=True,
        settings=settings,
        llm_probe=llm_probe_from_settings(settings),
        embed_probe=embed_probe_from_settings(settings),
    )
