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
