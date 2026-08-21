# backend/aipds/runner.py -- turn orchestration (in-process agent, no VM).
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import shutil
import time
from pathlib import Path, PurePosixPath
from typing import AsyncIterator

from aipds.models import AgentEvent
from aipds.globmatch import matches_glob
from aipds.pathsafe import reject_unsafe
from aipds.performance import log_performance
from aipds.s3store import S3StoreLike
from aipds.workspace_sync import SYNC_GLOBS, content_for_s3

_log = logging.getLogger(__name__)
_SYNC_CONCURRENCY = 8


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _interrupt_id_from(payload: str | None) -> str | None:
    if not payload:
        return None
    try:
        value = json.loads(payload).get("interrupt_id")
    except (json.JSONDecodeError, AttributeError):
        return None
    return value if isinstance(value, str) else None


class AgentRunner:
    """One turn executor per project. File-contract ops go straight to durable S3
    (nothing to boot). A turn restores S3 -> local workspace, runs the in-process
    agent, then syncs local -> S3 on done/error. There is no VM and no boot state
    machine: the local directory is disposable and is rebuilt from S3 at the start
    of every turn (S3 = source of truth)."""

    #: Owned by workspace_sync -- the publish-on-write path (claude_driver's
    #: PostToolUse hook) and this batch sync must upload **the same set**. Two
    #: copies of the set means a file present in only one of them shows up as a
    #: "document that appears and then vanishes".
    _SYNC_GLOBS = SYNC_GLOBS
    _RESTORE_PREFIXES = ("aiplc-docs/", "prototype/", "uploads/")

    def __init__(self, project_id, driver, s3: S3StoreLike, local_root: Path, session: dict):
        self.project_id = project_id
        self._driver = driver
        self._s3 = s3
        self._local_root = Path(local_root)
        self._session = session
        self._turn_active = False
        #: Identity of the turn holding the slot. A bare flag is not enough
        #: because `reattach` can preempt: when the preempted turn's finally runs
        #: later and clears the flag, a new turn slips in while the reattached
        #: stream is still going. The driver carries the same discipline for the
        #: same reason (ClaudeDriver._acquire_turn/_release_turn).
        self._turn_token: object | None = None
        self._pending_interrupt_id: str | None = None
        self._remote_etags: dict[str, str | None] | None = None
        self._synced_hashes: dict[str, str] = {}
        self.input_holder: str | None = None
        set_callback = getattr(driver, "set_file_published_callback", None)
        if set_callback is not None:
            set_callback(self._record_published_file)

    def set_input_holder(self, holder: str | None) -> None:
        self.input_holder = holder

    # ---- file-as-contract ops: straight to durable S3 ----

    async def read_file(self, rel_path: str) -> str:
        reject_unsafe(rel_path)
        return await self._s3.get(rel_path)

    async def write_file(self, rel_path: str, content: str) -> None:
        reject_unsafe(rel_path)
        await self._s3.put(rel_path, content)

    async def write_file_if_absent(self, rel_path: str, content: str) -> bool:
        """Upload path only: never silently replace an existing key."""
        reject_unsafe(rel_path)
        return await self._s3.put_if_absent(rel_path, content)

    async def list_files(self, glob: str) -> list[str]:
        reject_unsafe(glob)
        keys = await self._s3.list(_glob_prefix(glob))
        return sorted(k for k in keys if matches_glob(k, glob))

    async def list_files_newest_first(self, glob: str) -> list[str]:
        """Same glob as `list_files`, ordered by last-modified descending.

        Uses the `LastModified` that `list_objects_v2` already returns
        (S3Store.list_with_times) -- no extra call. Callers that need alphabetical
        order keep using `list_files`.
        """
        reject_unsafe(glob)
        pairs = await self._s3.list_with_times(_glob_prefix(glob))
        matched = [(k, t) for k, t in pairs if matches_glob(k, glob)]
        # Path as the secondary key -- entries sharing a timestamp must not
        # reorder between runs.
        matched.sort(key=lambda item: (-item[1], item[0]))
        return [k for k, _ in matched]

    # ---- workspace <-> S3 ----

    def _local_path(self, key: str) -> Path:
        reject_unsafe(key)
        return self._local_root / key

    async def _list_with_etags(
        self, prefix: str,
    ) -> list[tuple[str, str | None]]:
        list_with_etags = getattr(self._s3, "list_with_etags", None)
        if list_with_etags is not None:
            return await list_with_etags(prefix)
        # Compatibility for small test/application adapters that implement the
        # older protocol. A missing ETag deliberately forces a GET every turn.
        return [(key, None) for key in await self._s3.list(prefix)]

    def _record_published_file(
        self, key: str, content: str, etag: str | None,
    ) -> None:
        """Remember a successful PostToolUse/turn-end upload."""
        self._synced_hashes[key] = _content_hash(content)
        if etag is not None and self._remote_etags is not None:
            self._remote_etags[key] = etag

    async def _restore_workspace_from_s3(self) -> None:
        """Copy the durable workspace (S3 = source of truth) onto the local FS.
        The first turn downloads everything; later turns download only the files
        whose ETag changed."""
        started = time.perf_counter()
        cold = self._remote_etags is None
        metadata_groups = await asyncio.gather(
            *(self._list_with_etags(prefix)
              for prefix in self._RESTORE_PREFIXES))
        remote = {
            key: etag
            for group in metadata_groups
            for key, etag in group
        }
        previous = self._remote_etags or {}
        removed = set(previous) - set(remote)
        for key in removed:
            path = self._local_path(key)
            if path.is_file():
                path.unlink()
            self._synced_hashes.pop(key, None)

        keys = [
            key for key, etag in remote.items()
            if cold
            or etag is None
            or previous.get(key) != etag
            or not self._local_path(key).is_file()
        ]
        bodies = await asyncio.gather(*(self._s3.get(key) for key in keys))
        for key, body in zip(keys, bodies):
            p = self._local_path(key)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
            self._synced_hashes[key] = _content_hash(body)
        self._remote_etags = remote
        log_performance(
            _log,
            str(self.project_id),
            "restore",
            started,
            cold=str(cold).lower(),
            listed=len(remote),
            downloaded=len(keys),
            removed=len(removed),
        )

    async def _sync_workspace_to_s3(self) -> None:
        """Push the turn's output (methodology artifacts plus the prototype source
        subtree) from local up to durable S3. audit.md is redacted on the way in,
        so a direct S3 reader cannot see credentials."""
        started = time.perf_counter()
        scanned = 0
        changed: list[tuple[str, str]] = []
        for path in self._local_root.rglob("*"):
            if not path.is_file():
                continue
            key = path.relative_to(self._local_root).as_posix()
            if not any(matches_glob(key, glob) for glob in self._SYNC_GLOBS):
                continue
            scanned += 1
            reject_unsafe(key)
            content = content_for_s3(
                key, path.read_text(encoding="utf-8", errors="replace"))
            if self._synced_hashes.get(key) != _content_hash(content):
                changed.append((key, content))

        semaphore = asyncio.Semaphore(_SYNC_CONCURRENCY)
        uploaded = 0

        async def upload(key: str, content: str) -> None:
            nonlocal uploaded
            async with semaphore:
                etag = await self._s3.put(key, content)
            self._record_published_file(key, content, etag)
            uploaded += 1

        try:
            results = await asyncio.gather(
                *(upload(key, content) for key, content in changed),
                return_exceptions=True,
            )
            failure = next(
                (result for result in results
                 if isinstance(result, BaseException)),
                None,
            )
            if failure is not None:
                raise failure
        finally:
            log_performance(
                _log,
                str(self.project_id),
                "sync",
                started,
                scanned=scanned,
                changed=len(changed),
                uploaded=uploaded,
                concurrency=_SYNC_CONCURRENCY,
            )

    async def _sync_abandoned_turn(self) -> None:
        """Best-effort sync for a turn that never produced a terminal event.

        Deliberately swallows errors, unlike the fail-closed sync on the
        done/error path. This runs inside `finally`, where the turn is already
        being torn down for one of two reasons: the consumer walked away (an
        exception here would surface during generator cleanup, at a caller that
        is no longer listening) or the driver raised (an exception here would
        REPLACE that root-cause traceback with an S3 error). Neither is worth
        losing; the log entry is.
        """
        try:
            await self._sync_workspace_to_s3()
        except Exception:
            _log.exception("post-turn workspace sync failed: %s", self.project_id)

    # ---- turn relay ----

    def _claim_turn(self, *, preempt: bool = False) -> object | None:
        """Take the turn slot. Returns None if it is already held (unless `preempt`,
        which takes it away).

        A token is issued so that only the holder can release: `_finish_turn`
        checks it, so a preempted turn closing late and trying to release cannot
        free the new holder's slot.
        """
        if self._turn_active and not preempt:
            return None
        self._turn_active = True
        self._turn_token = object()
        return self._turn_token

    def _finish_turn(self, token: object | None) -> None:
        """Release the slot if this token is still the holder; otherwise no-op (which
        is the preempted case)."""
        if self._turn_token is token:
            self._turn_active = False
            self._turn_token = None

    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        token = self._claim_turn()
        if token is None:
            yield AgentEvent(kind="error", text="turn already in progress")
            return
        synced = False
        turn_started = time.perf_counter()
        first_event = False
        first_text = False
        try:
            self._local_root.mkdir(parents=True, exist_ok=True)
            await self._restore_workspace_from_s3()
            async for event in self._driver.run(text, self._session):
                if not first_event:
                    log_performance(
                        _log,
                        str(self.project_id), "first_agent_event", turn_started,
                        kind=event.kind)
                    first_event = True
                if not first_text and event.kind == "message" and event.text:
                    log_performance(
                        _log,
                        str(self.project_id), "first_text", turn_started)
                    first_text = True
                if event.kind == "questions":
                    got = _interrupt_id_from(event.payload)
                    if got:
                        self._pending_interrupt_id = got
                if event.kind in ("done", "error"):
                    # Sync BEFORE yielding the terminal event: a client that
                    # reads a doc the moment it sees `done` must not race the
                    # upload (fail-closed — a sync error surfaces instead of
                    # the terminal event).
                    await self._sync_workspace_to_s3()
                    synced = True
                yield event
        finally:
            self._finish_turn(token)
            # Backstop for every path that never reached the terminal event:
            # an SSE client disconnecting, a proxy timeout, the user navigating
            # away (GeneratorExit here), or the driver raising mid-turn. Without
            # this, files the agent already wrote stayed in the VOLATILE local
            # workspace only -- the doc panel showed an empty document and the
            # next refresh dropped it from the list, because S3 (the source of
            # truth for both) never received it.
            if not synced:
                await self._sync_abandoned_turn()
            log_performance(
                _log,
                str(self.project_id), "turn_total", turn_started,
                route="message", synced=str(synced).lower())

    async def reattach(self) -> AsyncIterator[AgentEvent]:
        """Relay a turn whose SSE consumer went away (sleep, screensaver, proxy).

        **This does NOT restore the workspace.** The other two paths *start* a
        turn, so S3 has to win there; here the agent is writing into that
        directory right now, and restoring would overwrite what it just wrote with
        S3's older copy. All this path does is watch.

        If the driver has no `run_live` (the Strands-era contract, or a test
        double) it finishes with a single `done` -- reattaching is a convenience,
        and its absence must not break the screen. The caller falls back to
        `GET /history` in that case.

        **It does not refuse on "already in progress".** An
        `if self._turn_active:` guard copied from the other three methods used to
        sit at the top. That flag means "a consumer is attached" -- see
        `ClaudeDriver.has_live_turn`'s docstring, which records both that meaning
        and the requirement that a reattaching browser must not be bounced -- and
        that is precisely the condition reattach has to **tolerate**. The guard is
        correct in the other three methods: those start a turn.

        Measured (2026-08-19, deployed instance): that guard put
        `turn already in progress` on the user's screen as agent speech.
        Instrumentation showed this guard firing first, with the driver's
        `has_live_turn()` never even consulted.

        **The slot is held by token.** When a preempted `send_message`'s finally
        runs later and clears the flag, a new turn slips in while the reattached
        stream is still going. The driver carries the same discipline for the same
        reason (`_acquire_turn`/`_release_turn`).
        """
        run_live = getattr(self._driver, "run_live", None)
        if run_live is None:
            yield AgentEvent(kind="done")
            return
        token = self._claim_turn(preempt=True)
        synced = False
        turn_started = time.perf_counter()
        try:
            async for event in run_live():
                if event.kind == "questions":
                    got = _interrupt_id_from(event.payload)
                    if got:
                        self._pending_interrupt_id = got
                if event.kind in ("done", "error"):
                    # Upload before the terminal event: a client that sees
                    # `done` reads the document immediately (same discipline as
                    # send_message).
                    await self._sync_workspace_to_s3()
                    synced = True
                yield event
        finally:
            self._finish_turn(token)
            if not synced:
                await self._sync_abandoned_turn()
            log_performance(
                _log,
                str(self.project_id), "turn_total", turn_started,
                route="reattach", synced=str(synced).lower())

    async def send_answers(self, answers: dict[str, str]) -> AsyncIterator[AgentEvent]:
        if self._pending_interrupt_id is None:
            yield AgentEvent(kind="error", text="no pending questions")
            return
        token = self._claim_turn()
        if token is None:
            yield AgentEvent(kind="error", text="turn already in progress")
            return
        synced = False
        turn_started = time.perf_counter()
        first_event = False
        first_text = False
        try:
            self._local_root.mkdir(parents=True, exist_ok=True)
            await self._restore_workspace_from_s3()
            interrupt_id, self._pending_interrupt_id = self._pending_interrupt_id, None
            async for event in self._driver.run_answers(interrupt_id, answers, self._session):
                if not first_event:
                    log_performance(
                        _log,
                        str(self.project_id), "first_agent_event", turn_started,
                        kind=event.kind)
                    first_event = True
                if not first_text and event.kind == "message" and event.text:
                    log_performance(
                        _log,
                        str(self.project_id), "first_text", turn_started)
                    first_text = True
                if event.kind == "questions":
                    got = _interrupt_id_from(event.payload)
                    if got:
                        self._pending_interrupt_id = got
                if event.kind in ("done", "error"):
                    await self._sync_workspace_to_s3()
                    synced = True
                yield event
        finally:
            self._finish_turn(token)
            if not synced:
                await self._sync_abandoned_turn()  # see send_message's note
            log_performance(
                _log,
                str(self.project_id), "turn_total", turn_started,
                route="answers", synced=str(synced).lower())

    async def interrupt(self) -> None:
        """Interrupt the turn in progress. Delegated to the driver.

        The turn slot is left alone: the running run() releases it itself when it
        emits its terminal event. Releasing here too would be a double release.
        """
        interrupt = getattr(self._driver, "interrupt", None)
        if interrupt is None:
            return  # Optional, outside the contract -- absent means no-op
                    # (still idempotent)
        await interrupt()

    async def pending(self) -> str | None:
        try:
            payload = await self._driver.pending(self._session)
        except Exception:
            _log.exception("pending probe failed")
            return None
        got = _interrupt_id_from(payload)
        if got:
            self._pending_interrupt_id = got
        return payload

    async def stop(self) -> None:
        """Clean up the local workspace and shut the driver down. Durable S3 is left
        alone -- deletion is projects.py's delete_project_data.

        The driver's disconnect() is **optional**, outside the contract
        (run/run_answers/pending). ClaudeDriver holds a claude subprocess, so
        skipping this call leaks that process for the lifetime of the backend
        (~300-500MB) every time a project is deleted. We probe for it with getattr
        and skip quietly when absent; the reason for keeping that defence even now
        that there is only one driver is to avoid mixing the contract (the three
        methods runner.py uses) with one implementation's convenience."""
        disconnect = getattr(self._driver, "disconnect", None)
        if disconnect is not None:
            try:
                await disconnect()
            except Exception:
                _log.exception("driver disconnect failed for %s", self.project_id)
        await asyncio.to_thread(shutil.rmtree, self._local_root, ignore_errors=True)


def _glob_prefix(glob: str) -> str:
    """The leading static (wildcard-free) directory part of a glob = the S3 list
    prefix. 'aiplc-docs/**/*-q.md' -> 'aiplc-docs/', 'aiplc-docs/audit.md' ->
    itself."""
    parts = PurePosixPath(glob).parts
    static: list[str] = []
    for part in parts:
        if any(ch in part for ch in "*?["):
            break
        static.append(part)
    prefix = "/".join(static)
    if not static:
        return ""
    if len(static) == len(parts):
        return prefix
    return prefix + "/"
