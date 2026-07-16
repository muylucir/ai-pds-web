# backend/pathfinder/sandbox/local.py
from __future__ import annotations
from pathlib import Path
from typing import AsyncIterator, Callable
from pathfinder.sandbox.base import Sandbox, AgentEvent

AgentScript = Callable[[str, "LocalSandbox"], list[AgentEvent]]

def _default_script(text: str, sb: "LocalSandbox") -> list[AgentEvent]:
    return [AgentEvent(kind="message", text=f"echo: {text}"), AgentEvent(kind="done")]

class LocalSandbox(Sandbox):
    def __init__(self, root: Path, script: AgentScript | None = None):
        self.root = Path(root)
        self._script = script or _default_script

    def _resolve(self, rel_path: str) -> Path:
        if rel_path.startswith("/") or ".." in Path(rel_path).parts:
            raise ValueError(f"unsafe path: {rel_path}")
        return self.root / rel_path

    async def start(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    async def read_file(self, rel_path: str) -> str:
        return self._resolve(rel_path).read_text(encoding="utf-8")

    async def write_file(self, rel_path: str, content: str) -> None:
        p = self._resolve(rel_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    async def list_files(self, glob: str) -> list[str]:
        return [str(p.relative_to(self.root)) for p in self.root.glob(glob) if p.is_file()]

    # Deliberate: this is an async-generator function (uses `yield`), even though the
    # Sandbox ABC declares send_message as a plain method returning AsyncIterator. Calling
    # an async-gen function returns an AsyncIterator synchronously (no await needed), so
    # `async for event in sandbox.send_message(text)` works. Do not "fix" this mismatch.
    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        for event in self._script(text, self):
            yield event

    async def stop(self) -> None:
        pass
