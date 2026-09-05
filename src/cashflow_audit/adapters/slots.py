from __future__ import annotations

import asyncio
import time
from collections.abc import Coroutine
from typing import TypeVar

from cashflow_audit.ports.protocols import JobBus, SlotKind

T = TypeVar("T")


class AlwaysGrant:
    def acquire(self, kind: SlotKind, timeout_sec: float = 0) -> bool:
        return True

    def release(self, kind: SlotKind) -> None:
        return None


class BusSlotGate:
    def __init__(self, bus: JobBus, audit_id: str, loop: asyncio.AbstractEventLoop) -> None:
        self._bus = bus
        self._audit_id = audit_id
        self._loop = loop

    def acquire(self, kind: SlotKind, timeout_sec: float = 0) -> bool:
        return self._run(self._wait_slot(kind, timeout_sec))

    def release(self, kind: SlotKind) -> None:
        self._run(self._bus.release_slot(kind, self._audit_id))

    def progress(self, stage: str) -> None:
        self._run(self._bus.set_progress(self._audit_id, stage))

    def _run(self, coro: Coroutine[object, object, T]) -> T:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    async def _wait_slot(self, kind: SlotKind, timeout_sec: float) -> bool:
        deadline = time.monotonic() + timeout_sec
        while True:
            if await self._bus.try_slot(kind, self._audit_id):
                return True
            if timeout_sec <= 0 or time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.05)
