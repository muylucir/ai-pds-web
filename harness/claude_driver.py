# harness/claude_driver.py  (runs INSIDE the MicroVM)
from __future__ import annotations
import asyncio
import json
from pathlib import PurePosixPath
from typing import AsyncIterator, Literal
from pydantic import BaseModel

# Mirror of backend/pathfinder/sandbox/base.py AgentEvent. The harness is a
# separate deployable and cannot import the backend package; these fields MUST
# stay identical to the backend model (kind/text/path) or the SSE contract breaks.
class AgentEvent(BaseModel):
    kind: Literal["message", "file_changed", "status", "done", "error"]
    text: str | None = None
    path: str | None = None

_FILE_TOOLS = {"Write", "Edit", "MultiEdit"}


def _rel(path: str, workspace: str) -> str:
    """Make a tool's file_path workspace-relative; leave already-relative paths."""
    ws = PurePosixPath(workspace)
    p = PurePosixPath(path)
    try:
        return str(p.relative_to(ws))
    except ValueError:
        return path.lstrip("/")


def translate(obj: dict, workspace: str) -> AgentEvent | None:
    """Map one Claude Code stream-json object to an AgentEvent, or None."""
    typ = obj.get("type")
    if typ == "result":
        return AgentEvent(kind="done")
    if typ == "assistant":
        for block in obj.get("message", {}).get("content", []):
            btype = block.get("type")
            if btype == "text":
                return AgentEvent(kind="message", text=block.get("text"))
            if btype == "tool_use":
                name = block.get("name", "")
                if name in _FILE_TOOLS:
                    fp = block.get("input", {}).get("file_path", "")
                    return AgentEvent(kind="file_changed", path=_rel(fp, workspace))
                return AgentEvent(kind="status", text=name)
    return None


class ClaudeDriver:
    """Spawns the Claude Code CLI and yields AgentEvents. First turn falls back
    to a new session (no --continue); subsequent turns pass --continue."""

    def __init__(self, workspace: str, claude_bin: str = "claude"):
        self._workspace = workspace
        self._claude = claude_bin

    def _argv(self, text: str, continue_session: bool) -> list[str]:
        argv = [self._claude, "-p", text,
                "--output-format", "stream-json", "--verbose",
                "--dangerously-skip-permissions"]
        if continue_session:
            argv.append("--continue")
        return argv

    async def run(self, text: str, *, continue_session: bool) -> AsyncIterator[AgentEvent]:
        proc = await asyncio.create_subprocess_exec(
            *self._argv(text, continue_session),
            cwd=self._workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdout is not None
        saw_done = False
        async for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                yield AgentEvent(kind="error", text="unparseable stream-json line")
                await proc.wait()
                return
            ev = translate(obj, self._workspace)
            if ev is not None:
                if ev.kind == "done":
                    saw_done = True
                yield ev
        rc = await proc.wait()
        if rc != 0:
            yield AgentEvent(kind="error", text=f"claude exited {rc}")
        elif not saw_done:
            yield AgentEvent(kind="done")
