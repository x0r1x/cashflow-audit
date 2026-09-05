from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from cashflow_audit.ports.protocols import ChatPort, EmbedPort, JobBus
from cashflow_audit.store.disk import DiskStore

MAX_UPLOAD_BYTES = 250 * 1024 * 1024


@dataclass
class AppContext:
    store: DiskStore
    bus: JobBus
    chat: ChatPort | None = None
    embed: EmbedPort | None = None
    run_workers: bool = False
    worker_concurrency: int = 4
    max_upload_bytes: int = MAX_UPLOAD_BYTES
    llm_ok: bool | None = None
    embed_ok: bool | None = None
    ttl_days: int = 14
    stopped: bool = False
    worker_tasks: list = field(default_factory=list)

    @property
    def data_root(self) -> Path:
        return self.store.root
