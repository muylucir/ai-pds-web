# backend/pathfinder/sandbox/base.py
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import AsyncIterator, Literal
from pydantic import BaseModel

class AgentEvent(BaseModel):
    kind: Literal["message", "file_changed", "status", "done", "error"]
    text: str | None = None
    path: str | None = None

class TurnResult(BaseModel):
    events: list[AgentEvent]

class Sandbox(ABC):
    # Soft "current input holder" hint (design §4): advisory metadata about who
    # holds the input turn in a facilitated session. Concrete no-op default so
    # every implementation (LocalSandbox, MicroVMSandbox) is polymorphically
    # safe — a route may read/set it off any Sandbox without AttributeError.
    # NOT enforcement and NOT turn serialization (that is send_message's busy
    # signal); purely advisory.
    input_holder: str | None = None

    def set_input_holder(self, holder: str | None) -> None:
        self.input_holder = holder

    @abstractmethod
    async def start(self) -> None: ...
    @abstractmethod
    async def read_file(self, rel_path: str) -> str: ...
    @abstractmethod
    async def write_file(self, rel_path: str, content: str) -> None: ...
    @abstractmethod
    async def list_files(self, glob: str) -> list[str]: ...
    @abstractmethod
    def send_message(self, text: str) -> AsyncIterator[AgentEvent]: ...
    @abstractmethod
    async def stop(self) -> None: ...
