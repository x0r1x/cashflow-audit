from __future__ import annotations

import asyncio

from cashflow_audit.ports.protocols import BudgetKind, Job, JobState, SlotKind


class MemoryJobBus:
    def __init__(
        self,
        *,
        redis_up: bool = True,
        max_run: int = 4,
        max_llm: int = 1,
        max_embed: int = 4,
        max_llm_calls: int = 20,
        max_embed_calls: int = 4,
    ) -> None:
        self.redis_up = redis_up
        self.enqueue_calls: list[str] = []
        self.live: dict[str, JobState] = {}
        self.pending: set[str] = set()
        self.locks: set[str] = set()
        self.glossary_locks: set[str] = set()
        self.taxonomy_locked = False
        self.touches: list[str] = []
        self.inflight: dict[str, set[str]] = {"run": set(), "llm": set(), "embed": set()}
        self.max = {"run": max_run, "llm": max_llm, "embed": max_embed}
        self.max_calls = {"llm": max_llm_calls, "embed": max_embed_calls}
        self.budget: dict[tuple[str, str], int] = {}
        self._q: asyncio.Queue[str] | None = None

    def _queue(self) -> asyncio.Queue[str]:
        if self._q is None:
            self._q = asyncio.Queue()
        return self._q

    async def ping(self) -> bool:
        return self.redis_up

    async def enqueue(self, audit_id: str) -> None:
        if audit_id in self.pending:
            return
        self.pending.add(audit_id)
        self.enqueue_calls.append(audit_id)
        await self._queue().put(audit_id)

    async def claim(self, consumer: str = "worker") -> Job | None:
        _ = consumer
        try:
            audit_id = self._queue().get_nowait()
        except asyncio.QueueEmpty:
            return None
        return Job(audit_id=audit_id, message_id=audit_id)

    async def ack(self, job: Job) -> None:
        self.pending.discard(job.audit_id)

    async def set_progress(self, audit_id: str, stage: str) -> None:
        prev = self.live.get(audit_id)
        actor = prev.actor_id if prev else None
        self.live[audit_id] = JobState(
            status="running", stage=stage, error=None, actor_id=actor
        )

    async def get_live(self, audit_id: str) -> JobState | None:
        return self.live.get(audit_id)

    async def mark_queued(self, audit_id: str, actor_id: str) -> None:
        self.live[audit_id] = JobState(
            status="queued", stage="queued", error=None, actor_id=actor_id
        )

    async def set_terminal(
        self,
        audit_id: str,
        status: str,
        *,
        stage: str = "done",
        error: str | None = None,
    ) -> None:
        prev = self.live.get(audit_id)
        actor = prev.actor_id if prev else None
        self.live[audit_id] = JobState(
            status=status, stage=stage, error=error, actor_id=actor
        )

    async def acquire_audit(self, audit_id: str) -> bool:
        if audit_id in self.locks:
            return False
        self.locks.add(audit_id)
        return True

    async def release_audit(self, audit_id: str) -> None:
        self.locks.discard(audit_id)

    async def try_slot(self, kind: SlotKind, member: str) -> bool:
        bucket = self.inflight[kind]
        if member in bucket:
            return True
        if len(bucket) >= self.max[kind]:
            return False
        bucket.add(member)
        return True

    async def release_slot(self, kind: SlotKind, member: str) -> None:
        self.inflight[kind].discard(member)

    async def acquire_glossary(self, actor_id: str) -> bool:
        if actor_id in self.glossary_locks:
            return False
        self.glossary_locks.add(actor_id)
        return True

    async def release_glossary(self, actor_id: str) -> None:
        self.glossary_locks.discard(actor_id)

    async def charge(self, audit_id: str, kind: BudgetKind) -> bool:
        cap = self.max_calls[kind]
        key = (audit_id, kind)
        used = self.budget.get(key, 0) + 1
        if used > cap:
            return False
        self.budget[key] = used
        return True

    async def touch_audit(self, audit_id: str) -> None:
        self.touches.append(audit_id)

    async def acquire_taxonomy(self) -> bool:
        if self.taxonomy_locked:
            return False
        self.taxonomy_locked = True
        return True

    async def release_taxonomy(self) -> None:
        self.taxonomy_locked = False
