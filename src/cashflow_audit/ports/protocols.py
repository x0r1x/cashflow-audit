from __future__ import annotations

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
