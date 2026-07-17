# backend/pathfinder/sandbox/microvm.py
from __future__ import annotations
import asyncio
from typing import AsyncIterator, Callable, Protocol
from pathfinder.sandbox.base import Sandbox, AgentEvent
from pathfinder.sandbox.pathsafe import reject_unsafe
from pathfinder.sandbox.microvm_control import MicroVMController, BootSpec, VMHandle

class HarnessLike(Protocol):
    def send_message(self, text: str) -> AsyncIterator[AgentEvent]: ...
    async def read_file(self, rel_path: str) -> str: ...
    async def write_file(self, rel_path: str, content: str) -> None: ...
    async def list_files(self, glob: str) -> list[str]: ...
    async def heartbeat(self) -> bool: ...

class MicroVMSandbox(Sandbox):
    """Real sandbox: boots a Claude Code MicroVM (with aiplc-rules baked into
    the image) and relays turns over the harness. Implements the Sandbox ABC
    exactly, so it drops into make_sandbox with zero route changes.

    Part 1 scope: file ops lazily boot the VM and use the live harness. Part 2
    reroutes not-booted file ops to S3 and syncs after each turn. No
    methodology/resume logic lives here — session-continuity is the rule's job.
    """

    def __init__(
        self,
        project_id: str,
        controller: MicroVMController,
        spec: BootSpec,
        harness_factory: Callable[[VMHandle], HarnessLike],
    ):
        self.project_id = project_id
        self._controller = controller
        self._spec = spec
        self._harness_factory = harness_factory
        self._handle: VMHandle | None = None
        self._harness: HarnessLike | None = None
        self._boot_lock = asyncio.Lock()
        self._turn_active = False

    async def start(self) -> None:
        # Lazy: do NOT boot here. A project can exist with no live MicroVM until
        # first needed. "Not yet booted" == self._handle is None.
        self._handle = None
        self._harness = None

    async def _ensure_ready(self) -> HarnessLike:
        async with self._boot_lock:
            if self._handle is None:
                self._handle = await self._controller.boot(self.project_id, self._spec)
                self._harness = self._harness_factory(self._handle)
            elif self._handle.status == "suspended":
                self._handle = await self._controller.resume(self._handle)
                self._harness = self._harness_factory(self._handle)
            assert self._harness is not None
            return self._harness

    async def read_file(self, rel_path: str) -> str:
        reject_unsafe(rel_path)
        harness = await self._ensure_ready()
        return await harness.read_file(rel_path)

    async def write_file(self, rel_path: str, content: str) -> None:
        reject_unsafe(rel_path)
        harness = await self._ensure_ready()
        await harness.write_file(rel_path, content)

    async def list_files(self, glob: str) -> list[str]:
        reject_unsafe(glob)
        harness = await self._ensure_ready()
        return await harness.list_files(glob)

    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        # Single Claude Code session per project: serialize turns. A concurrent
        # turn gets a clear soft busy signal (no hard queue).
        if self._turn_active:
            yield AgentEvent(kind="error", text="turn already in progress")
            return
        self._turn_active = True
        try:
            harness = await self._ensure_ready()
            async for event in harness.send_message(text):
                yield event
            # Part 2 hook: after the terminal event, sync workspace -> S3 here.
        finally:
            self._turn_active = False

    async def stop(self) -> None:
        if self._handle is not None:
            await self._controller.stop(self._handle)
        self._handle = None
        self._harness = None
