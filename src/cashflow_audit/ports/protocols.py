from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel


class EmbedPort(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class ChatPort(Protocol):
    def complete_json(self, schema: type[BaseModel], messages: list) -> BaseModel: ...


SlotKind = Literal["llm", "embed", "run"]


class SlotGate(Protocol):
    def acquire(self, kind: SlotKind, timeout_sec: float = 0) -> bool: ...

    def release(self, kind: SlotKind) -> None: ...


BudgetKind = Literal["llm", "embed"]


class BudgetPort(Protocol):
    def charge(self, kind: BudgetKind) -> bool: ...


@dataclass
class Job:
    audit_id: str
    message_id: str = ""


@dataclass
class JobState:
    status: str
    stage: str
    error: str | None = None
    actor_id: str | None = None


class JobBus(Protocol):
    async def ping(self) -> bool: ...

    async def enqueue(self, audit_id: str) -> None: ...

    async def claim(self, consumer: str = "worker") -> Job | None: ...

    async def ack(self, job: Job) -> None: ...

    async def set_progress(self, audit_id: str, stage: str) -> None: ...

    async def get_live(self, audit_id: str) -> JobState | None: ...

    async def mark_queued(self, audit_id: str, actor_id: str) -> None: ...

    async def set_terminal(
        self,
        audit_id: str,
        status: str,
        *,
        stage: str = "done",
        error: str | None = None,
    ) -> None: ...

    async def acquire_audit(self, audit_id: str) -> bool: ...

    async def release_audit(self, audit_id: str) -> None: ...

    async def try_slot(self, kind: SlotKind, member: str) -> bool: ...

    async def release_slot(self, kind: SlotKind, member: str) -> None: ...

    async def acquire_glossary(self, actor_id: str) -> bool: ...

    async def release_glossary(self, actor_id: str) -> None: ...

    async def charge(self, audit_id: str, kind: BudgetKind) -> bool: ...

    async def touch_audit(self, audit_id: str) -> None: ...

    async def acquire_taxonomy(self) -> bool: ...

    async def release_taxonomy(self) -> None: ...


class AuditStore(Protocol):
    def dest_dir(self, audit_id: str) -> Path: ...

    def glossary_dir(self) -> Path: ...
