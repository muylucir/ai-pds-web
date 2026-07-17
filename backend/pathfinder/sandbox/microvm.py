# backend/pathfinder/sandbox/microvm.py
from __future__ import annotations
import asyncio
import fnmatch
from pathlib import PurePosixPath
from typing import AsyncIterator, Callable, Protocol
from pathfinder.sandbox.base import Sandbox, AgentEvent
from pathfinder.sandbox.pathsafe import reject_unsafe
from pathfinder.sandbox.microvm_control import MicroVMController, BootSpec, VMHandle
from pathfinder.sandbox.s3store import S3StoreLike


class HarnessLike(Protocol):
    def send_message(self, text: str) -> AsyncIterator[AgentEvent]: ...
    async def read_file(self, rel_path: str) -> str: ...
    async def write_file(self, rel_path: str, content: str) -> None: ...
    async def list_files(self, glob: str) -> list[str]: ...
    async def heartbeat(self) -> bool: ...


def _glob_prefix(glob: str) -> str:
    """The leading static (wildcard-free) directory portion of a glob, used as
    the S3 list prefix. e.g. 'aiplc-docs/**/*-questions.md' -> 'aiplc-docs/',
    'aiplc-docs/audit.md' -> 'aiplc-docs/audit.md', '*.md' -> ''."""
    parts = PurePosixPath(glob).parts
    static: list[str] = []
    for part in parts:
        if any(ch in part for ch in "*?["):
            break
        static.append(part)
    prefix = "/".join(static)
    if not static:                       # glob starts with a wildcard, e.g. '*.md'
        return ""
    if len(static) == len(parts):        # no wildcard at all: a literal path key
        return prefix
    return prefix + "/"                   # static leading dirs before a wildcard


class MicroVMSandbox(Sandbox):
    """Real sandbox: boots a Claude Code MicroVM (aiplc-rules baked into the
    image) for turns, and uses a durable S3 store as the source of truth for
    all file-as-contract ops. File ops NEVER boot the VM (true laziness): a
    project's aiplc-docs is read/written against S3 with no live MicroVM. The
    VM boots only for send_message (a turn). After each turn the workspace is
    synced VM -> S3 (Task 4); on resume/recovery S3-newer files are pushed
    S3 -> VM (Tasks 5/6). No methodology/resume logic lives here — the
    session-continuity rule resumes itself by reading aiplc-state.md.
    """

    def __init__(
        self,
        project_id: str,
        controller: MicroVMController,
        spec: BootSpec,
        harness_factory: Callable[[VMHandle], HarnessLike],
        s3: S3StoreLike,
    ):
        self.project_id = project_id
        self._controller = controller
        self._spec = spec
        self._harness_factory = harness_factory
        self._s3 = s3
        self._handle: VMHandle | None = None
        self._harness: HarnessLike | None = None
        self._boot_lock = asyncio.Lock()
        self._turn_active = False

    async def start(self) -> None:
        # Lazy: do NOT boot. "Not yet booted" == self._handle is None.
        self._handle = None
        self._harness = None

    # ---- file-as-contract ops: ALWAYS durable S3, never boot ----

    async def read_file(self, rel_path: str) -> str:
        reject_unsafe(rel_path)
        return await self._s3.get(rel_path)

    async def write_file(self, rel_path: str, content: str) -> None:
        reject_unsafe(rel_path)
        await self._s3.put(rel_path, content)

    async def list_files(self, glob: str) -> list[str]:
        reject_unsafe(glob)
        keys = await self._s3.list(_glob_prefix(glob))
        return sorted(k for k in keys if fnmatch.fnmatch(k, glob))

    # ---- turn relay: boots the VM (Task 4 adds post-turn sync) ----

    _SYNC_GLOBS = ("aiplc-docs/**/*", "prototype/**/*")

    async def _sync_workspace_to_s3(self, harness: HarnessLike) -> None:
        """Pull the turn's output out of the VM FS into durable S3. Only the
        methodology output + prototype source subtrees (never the whole FS).
        Raw bytes are stored (source-of-truth); see the redaction-at-rest
        Open Question."""
        for glob in self._SYNC_GLOBS:
            for key in await harness.list_files(glob):
                reject_unsafe(key)
                content = await harness.read_file(key)
                await self._s3.put(key, content)

    _RESTORE_PREFIXES = ("aiplc-docs/", "prototype/")

    async def _restore_workspace_from_s3(self, harness: HarnessLike) -> None:
        """Copy the durable workspace (S3 = source of truth) into the VM FS.
        Used to reconcile after resume (re-push writes that landed in S3 while
        suspended) AND to fully restore a freshly-booted VM after expiry/crash.
        S3 unconditionally wins; the push is idempotent. No methodology/resume
        logic here — we only copy files; the session-continuity rule reads
        aiplc-state.md and resumes itself once the VM is running."""
        for prefix in self._RESTORE_PREFIXES:
            for key in await self._s3.list(prefix):
                reject_unsafe(key)
                await harness.write_file(key, await self._s3.get(key))

    async def _boot_and_restore(self) -> HarnessLike:
        self._handle = await self._controller.boot(self.project_id, self._spec)
        self._harness = self._harness_factory(self._handle)   # mint-on-boot (JWE)
        await self._restore_workspace_from_s3(self._harness)
        return self._harness

    async def _ensure_ready(self) -> HarnessLike:
        async with self._boot_lock:
            if self._handle is None:
                return await self._boot_and_restore()
            # Finding A (a): refresh the LIVE status before trusting the cache.
            current = await self._controller.status(self._handle)
            if current == "ready":
                assert self._harness is not None
                return self._harness
            if current == "suspended":
                self._handle = await self._controller.resume(self._handle)
                self._harness = self._harness_factory(self._handle)   # mint-on-resume (JWE)
                await self._restore_workspace_from_s3(self._harness)  # (c) reconcile
                return self._harness
            # "expired"/"stopped": the VM (and its FS) are gone — reboot fresh
            # and fully restore from S3 (Task 6's recovery scenario).
            self._handle = None
            return await self._boot_and_restore()

    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        if self._turn_active:
            yield AgentEvent(kind="error", text="turn already in progress")
            return
        self._turn_active = True
        try:
            harness = await self._ensure_ready()
            async for event in harness.send_message(text):
                yield event
            # Durable persistence: after the turn's terminal event, sync the
            # workspace out of the VM into S3 so expiry/crash loses nothing and
            # the next route read sees current data.
            await self._sync_workspace_to_s3(harness)
        finally:
            self._turn_active = False

    async def stop(self) -> None:
        if self._handle is not None:
            await self._controller.stop(self._handle)
        self._handle = None
        self._harness = None
