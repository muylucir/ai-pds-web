# backend/tests/fakes/in_memory_harness.py
from __future__ import annotations
import fnmatch
from typing import AsyncIterator
from pathfinder.sandbox.base import AgentEvent

def _matches_glob(path: str, glob: str) -> bool:
    """fnmatch-based glob matching that treats '**' with pathlib.Path.glob
    semantics: '**' matches ZERO or more path segments, so 'dir/**/*' matches
    both a direct child ('dir/audit.md') and a nested file
    ('dir/sub/audit.md'). Plain fnmatch.fnmatch requires '**' to consume at
    least one literal '/', so it silently misses top-level files under a
    '**'-globbed directory — a real bug this fixes (the production harness is
    backed by a real filesystem and pathlib.Path.glob, whose '**' already
    matches top-level files; this in-memory fake must match the same globs
    the same way). Globs without '**' fall back to plain fnmatch, unchanged,
    so existing single-level glob behavior (e.g. 'aiplc-docs/*-questions.md')
    is preserved exactly."""
    if "**" not in glob:
        return fnmatch.fnmatch(path, glob)
    prefix, _, suffix = glob.partition("**")
    suffix = suffix.lstrip("/") or "*"
    return path.startswith(prefix) and fnmatch.fnmatch(path[len(prefix):], suffix)

class FakeHarness:
    """In-memory object with the HarnessClient method surface, for
    MicroVMSandbox unit tests (no HTTP). `events_for` maps a message text to a
    canned event list; the default is an echo turn ending in `done`."""

    def __init__(self, events_for=None):
        self.files: dict[str, str] = {}
        self._events_for = events_for or (
            lambda text: [
                AgentEvent(kind="message", text=f"echo: {text}"),
                AgentEvent(kind="done"),
            ]
        )

    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        for ev in self._events_for(text):
            yield ev

    async def read_file(self, rel_path: str) -> str:
        if rel_path not in self.files:
            raise FileNotFoundError(rel_path)
        return self.files[rel_path]

    async def write_file(self, rel_path: str, content: str) -> None:
        self.files[rel_path] = content

    async def list_files(self, glob: str) -> list[str]:
        return sorted(p for p in self.files if _matches_glob(p, glob))

    async def heartbeat(self) -> bool:
        return True
