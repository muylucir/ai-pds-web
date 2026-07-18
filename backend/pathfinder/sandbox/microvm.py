# backend/pathfinder/sandbox/microvm.py
from __future__ import annotations
import asyncio
from pathlib import PurePosixPath
from typing import Awaitable, AsyncIterator, Callable, Protocol
from pathfinder.sandbox.base import Sandbox, AgentEvent
from pathfinder.sandbox.globmatch import matches_glob
from pathfinder.sandbox.pathsafe import reject_unsafe
from pathfinder.sandbox.microvm_control import MicroVMController, BootSpec, VMHandle
from pathfinder.sandbox.s3store import S3StoreLike
from pathfinder.parsers.redaction import redact_credentials


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
        on_stop: Callable[[], Awaitable[None]] | None = None,
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
        # I2: optional caller-owned cleanup hook (e.g. app.py's shared
        # httpx.AsyncClient captured in the harness_factory closure). Kept
        # generic (Awaitable[None] callback) so this module stays free of any
        # httpx coupling -- the sandbox doesn't know or care what it closes.
        self._on_stop = on_stop

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
        return sorted(k for k in keys if matches_glob(k, glob))

    # ---- turn relay: boots the VM (Task 4 adds post-turn sync) ----

    _SYNC_GLOBS = ("aiplc-docs/**/*", "prototype/**/*")

    async def _sync_workspace_to_s3(self, harness: HarnessLike) -> None:
        """Pull the turn's output out of the VM FS into durable S3. Only the
        methodology output + prototype source subtrees (never the whole FS).
        Raw bytes are stored (source-of-truth); see the redaction-at-rest
        Open Question."""
        for glob in self._SYNC_GLOBS:
            for key in await harness.list_files(glob):
                # Fail-closed by design: an unsafe key aborts the whole sync
                # loudly (raises) rather than being silently skipped. Do not
                # "fix" this into a silent skip -- a silently-dropped key
                # would look like a successful sync while quietly losing data.
                reject_unsafe(key)
                content = await harness.read_file(key)
                # redaction-at-rest: audit content is stored redacted in
                # durable S3; app-side reads redact anyway (parsers/audit.py,
                # routes/turns.py), so this removes exposure to direct S3
                # readers. Only audit.md -- other docs and prototype/** source
                # are stored raw (source-code fidelity; broader redaction-at-
                # rest remains a future security-review item). Known
                # consequence: _restore_workspace_from_s3 reconciles S3->VM on
                # every turn, so the redacted audit.md is pushed back into the
                # VM after the first sync, replacing the agent's raw version.
                # Accepted defense-in-depth -- session continuity reads
                # aiplc-state.md, not audit.md.
                if key == "aiplc-docs/audit.md":
                    content = redact_credentials(content)
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
                # C1: reconcile even on warm reuse -- a route may have written
                # straight to S3 (e.g. a facilitator's answer) while this VM
                # sat idle-but-ready; S3 unconditionally wins and the push is
                # idempotent, so every turn starts from "VM view == S3".
                await self._restore_workspace_from_s3(self._harness)
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
                if event.kind in ("done", "error"):
                    # I1: sync BEFORE yielding the terminal event, not after.
                    # A client reacting to `done` (e.g. re-reading a route
                    # file) must never race the sync and see pre-sync
                    # (stale) S3 -- so durable persistence must be complete
                    # by the time the terminal event reaches the caller. A
                    # sync failure here surfaces before `done` is delivered;
                    # that is the intended fail-closed behavior.
                    await self._sync_workspace_to_s3(harness)
                yield event
        finally:
            self._turn_active = False

    async def stop(self) -> None:
        # I2: on_stop (e.g. app.py's shared_http.aclose) is a caller-owned
        # resource this sandbox doesn't understand, so it runs after the
        # sandbox's own stop logic -- but inside `finally`, so a failure in
        # controller.stop() can never leak the caller's resource (a locally-
        # owned client leaking is worse than the two steps running out of
        # their "natural" order on the error path).
        try:
            if self._handle is not None:
                await self._controller.stop(self._handle)
        finally:
            self._handle = None
            self._harness = None
            if self._on_stop is not None:
                await self._on_stop()
