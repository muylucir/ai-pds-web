# harness/claude_driver.py  (runs INSIDE the MicroVM)
from __future__ import annotations
import asyncio
import contextlib
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
_STDERR_CHUNK = 65536


def _rel(path: str, workspace: str) -> str | None:
    """Make a tool's file_path workspace-relative; leave already-relative
    paths untouched. Returns None if the result would escape the workspace.

    `PurePosixPath.relative_to` does NOT normalize `..` segments: relativizing
    "/workspace/../etc/passwd" against "/workspace" yields "../etc/passwd" —
    syntactically "relative" but still an escape once a caller joins it back
    onto the workspace root. Any relativized result containing a `..`
    segment, or that is still absolute, is therefore rejected as an escape
    (None) rather than forwarded as a path.
    """
    ws = PurePosixPath(workspace)
    p = PurePosixPath(path)
    try:
        rel = p.relative_to(ws)
    except ValueError:
        rel = PurePosixPath(path.lstrip("/"))
    rel_str = str(rel)
    if ".." in rel.parts or rel_str.startswith("/"):
        return None
    return rel_str


def translate(obj: dict, workspace: str) -> list[AgentEvent]:
    """Map one Claude Code stream-json object to zero or more AgentEvents,
    in block order. Real assistant messages can carry several content
    blocks together (e.g. text + tool_use, or multiple parallel tool_use
    blocks) — every block must be translated, not just the first."""
    typ = obj.get("type")
    if typ == "result":
        return [AgentEvent(kind="done")]
    events: list[AgentEvent] = []
    if typ == "assistant":
        for block in obj.get("message", {}).get("content", []):
            btype = block.get("type")
            if btype == "text":
                events.append(AgentEvent(kind="message", text=block.get("text")))
            elif btype == "tool_use":
                name = block.get("name", "")
                if name in _FILE_TOOLS:
                    fp = block.get("input", {}).get("file_path", "")
                    rel = _rel(fp, workspace)
                    if rel is None:
                        events.append(AgentEvent(
                            kind="status", text="file outside workspace ignored"))
                    else:
                        events.append(AgentEvent(kind="file_changed", path=rel))
                else:
                    events.append(AgentEvent(kind="status", text=name))
    return events


async def _drain_stderr(stream: asyncio.StreamReader) -> None:
    """Continuously read and discard the child's stderr. If nobody reads a
    subprocess's stderr pipe, the OS pipe buffer fills and the child blocks
    on its next stderr write — which, for a CLI that writes stderr before or
    interleaved with stdout, silently deadlocks stdout production too. We
    intentionally never surface this content in an AgentEvent: the only
    thing that reaches the event stream is a bounded, credential-free
    "claude exited N" message built from the exit code alone."""
    while True:
        chunk = await stream.read(_STDERR_CHUNK)
        if not chunk:
            return


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
        assert proc.stderr is not None
        # Drain stderr concurrently so the child never blocks on a full pipe
        # buffer (see _drain_stderr docstring). Its content is discarded —
        # only the exit code, never raw stderr bytes, reaches an AgentEvent.
        stderr_task = asyncio.ensure_future(_drain_stderr(proc.stderr))
        try:
            saw_done = False
            async for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    yield AgentEvent(kind="error", text="unparseable stream-json line")
                    return
                for ev in translate(obj, self._workspace):
                    if ev.kind == "done":
                        saw_done = True
                    yield ev
            rc = await proc.wait()
            if rc != 0:
                yield AgentEvent(kind="error", text=f"claude exited {rc}")
            elif not saw_done:
                yield AgentEvent(kind="done")
        finally:
            # Reached on normal completion, on an unparseable-line early
            # return, AND on the generator being closed/cancelled mid-turn
            # (e.g. a caller stops iterating after the first event). In the
            # latter case the subprocess would otherwise keep running
            # unsupervised; kill it and reap it so no orphan `claude`
            # process survives the driver call.
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stderr_task
