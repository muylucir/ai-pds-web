# harness/claude_driver.py  (runs INSIDE the MicroVM)
from __future__ import annotations
import asyncio
import contextlib
import json
import logging
from pathlib import PurePosixPath
from typing import AsyncIterator, Literal
from pydantic import BaseModel

_log = logging.getLogger("harness.driver")

# Mirror of backend/pathfinder/sandbox/base.py AgentEvent. The harness is a
# separate deployable and cannot import the backend package; these fields MUST
# stay identical to the backend model (kind/text/path) or the SSE contract breaks.
class AgentEvent(BaseModel):
    kind: Literal["message", "file_changed", "status", "done", "error"]
    text: str | None = None
    path: str | None = None

_FILE_TOOLS = {"Write", "Edit", "MultiEdit"}
_STDERR_CHUNK = 65536
_STDERR_TAIL = 4096  # bytes of stderr retained for diagnostics on nonzero exit


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


async def _drain_stderr(stream: asyncio.StreamReader, tail: bytearray) -> None:
    """Continuously read the child's stderr so the OS pipe buffer never fills
    (an unread stderr pipe deadlocks stdout production too). We keep only a
    bounded TAIL (last _STDERR_TAIL bytes) for server-side diagnostics on a
    non-zero exit — it is logged to the harness logger (→ CloudWatch), NEVER
    surfaced in an AgentEvent to the user: the event stream still only ever
    carries the credential-free "claude exited N" built from the exit code."""
    while True:
        chunk = await stream.read(_STDERR_CHUNK)
        if not chunk:
            return
        tail.extend(chunk)
        if len(tail) > _STDERR_TAIL:
            del tail[:-_STDERR_TAIL]


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
        # buffer (see _drain_stderr docstring). A bounded tail is kept for
        # server-side diagnostics; only the exit code, never raw stderr bytes,
        # reaches an AgentEvent.
        stderr_tail = bytearray()
        stderr_task = asyncio.ensure_future(_drain_stderr(proc.stderr, stderr_tail))
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
                # Log the stderr tail server-side (→ CloudWatch) so a failed
                # turn is debuggable; the user-facing event stays exit-code-only.
                _log.error("claude exited %s; stderr tail: %s", rc,
                           stderr_tail.decode("utf-8", "replace").strip())
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
