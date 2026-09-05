from __future__ import annotations

from typing import Any

from cashflow_audit.ports.protocols import Job, JobState, SlotKind

STREAM = "cf:audits"
GROUP = "cf:audits:workers"

_SLOT_LUA = """
local key = KEYS[1]
local member = ARGV[1]
local cap = tonumber(ARGV[2])
if redis.call('SISMEMBER', key, member) == 1 then
  return 1
end
if redis.call('SCARD', key) >= cap then
  return 0
end
redis.call('SADD', key, member)
return 1
"""


class RedisJobBus:
    def __init__(
        self,
        client: Any,
        *,
        max_run: int = 4,
        max_llm: int = 1,
        max_embed: int = 4,
    ) -> None:
        self._r = client
        self.max = {"run": max_run, "llm": max_llm, "embed": max_embed}
        self._group_ready = False

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        max_run: int = 4,
        max_llm: int = 1,
        max_embed: int = 4,
    ) -> RedisJobBus:
        import redis.asyncio as redis

        return cls(
            redis.from_url(url, decode_responses=True),
            max_run=max_run,
            max_llm=max_llm,
            max_embed=max_embed,
        )

    async def ping(self) -> bool:
        try:
            return bool(await self._r.ping())
        except Exception:
            return False

    async def enqueue(self, audit_id: str) -> None:
        await self._ensure_group()
        added = await self._r.sadd("cf:pending", audit_id)
        if not added:
            return
        await self._r.xadd(STREAM, {"audit_id": audit_id})

    async def claim(self, consumer: str = "worker") -> Job | None:
        await self._ensure_group()
        claimed = await self._r.xautoclaim(
            STREAM, GROUP, consumer, 60000, "0-0", count=1
        )
        job = _job_from_xautoclaim(claimed)
        if job is not None:
            return job
        result = await self._r.xreadgroup(
            GROUP, consumer, streams={STREAM: ">"}, count=1, block=200
        )
        return _job_from_xread(result)

    async def ack(self, job: Job) -> None:
        if job.message_id:
            await self._r.xack(STREAM, GROUP, job.message_id)
        await self._r.srem("cf:pending", job.audit_id)

    async def set_progress(self, audit_id: str, stage: str) -> None:
        key = f"cf:job:{audit_id}"
        await self._r.hset(key, mapping={"status": "running", "stage": stage, "error": ""})
        await self._r.expire(f"cf:lock:audit:{audit_id}", 120)

    async def get_live(self, audit_id: str) -> JobState | None:
        data = await self._r.hgetall(f"cf:job:{audit_id}")
        if not data:
            return None
        error = data.get("error") or None
        return JobState(
            status=data.get("status") or "queued",
            stage=data.get("stage") or "queued",
            error=error,
            actor_id=data.get("actor_id") or None,
        )

    async def mark_queued(self, audit_id: str, actor_id: str) -> None:
        await self._r.hset(
            f"cf:job:{audit_id}",
            mapping={
                "status": "queued",
                "stage": "queued",
                "actor_id": actor_id,
                "error": "",
            },
        )

    async def set_terminal(
        self,
        audit_id: str,
        status: str,
        *,
        stage: str = "done",
        error: str | None = None,
    ) -> None:
        await self._r.hset(
            f"cf:job:{audit_id}",
            mapping={
                "status": status,
                "stage": stage,
                "error": error or "",
            },
        )
        await self._r.expire(f"cf:job:{audit_id}", 86400)
        await self._r.delete(f"cf:budget:{audit_id}:llm", f"cf:budget:{audit_id}:emb")

    async def acquire_audit(self, audit_id: str) -> bool:
        ok = await self._r.set(f"cf:lock:audit:{audit_id}", "1", nx=True, px=120_000)
        return bool(ok)

    async def release_audit(self, audit_id: str) -> None:
        await self._r.delete(f"cf:lock:audit:{audit_id}")

    async def try_slot(self, kind: SlotKind, member: str) -> bool:
        cap = self.max[kind]
        result = await self._r.eval(_SLOT_LUA, 1, f"cf:inflight:{kind}", member, cap)
        return bool(result)

    async def release_slot(self, kind: SlotKind, member: str) -> None:
        await self._r.srem(f"cf:inflight:{kind}", member)

    async def acquire_glossary(self, actor_id: str) -> bool:
        ok = await self._r.set(f"cf:lock:glossary:{actor_id}", "1", nx=True, px=30_000)
        return bool(ok)

    async def release_glossary(self, actor_id: str) -> None:
        await self._r.delete(f"cf:lock:glossary:{actor_id}")

    async def _ensure_group(self) -> None:
        if self._group_ready:
            return
        try:
            await self._r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._group_ready = True


def _job_from_xread(result: Any) -> Job | None:
    if not result:
        return None
    _stream, messages = result[0]
    if not messages:
        return None
    message_id, fields = messages[0]
    audit_id = fields.get("audit_id")
    if not audit_id:
        return None
    return Job(audit_id=audit_id, message_id=message_id)


def _job_from_xautoclaim(result: Any) -> Job | None:
    if not result:
        return None
    messages = result[1] if isinstance(result, list | tuple) and len(result) > 1 else None
    if not messages:
        return None
    message_id, fields = messages[0]
    audit_id = fields.get("audit_id")
    if not audit_id:
        return None
    return Job(audit_id=audit_id, message_id=message_id)
