# backend/aipds/runner.py — turn orchestration (in-process agent, no VM).
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import shutil
import time
from pathlib import Path, PurePosixPath
from typing import AsyncIterator

from aipds import audit_log
from aipds.audit_log import UserInput
from aipds.context_usage import DISCOVERY_KEY, ContextRecord
from aipds.models import AgentEvent
from aipds.globmatch import matches_glob
from aipds.pathsafe import (read_text_beneath, reject_unsafe, stat_beneath,
                             unlink_beneath, write_beneath)
from aipds.performance import log_performance
from aipds.s3store import S3StoreLike
from aipds.workspace_sync import SYNC_GLOBS, content_for_s3, publish_file

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
    """Per-project turn runner. File-contract ops go directly to durable S3 (no boot). A
    turn restores S3 → local workspace, runs the in-process agent, and on done/error
    syncs local → S3. There is no VM/boot state machine -- the local directory is
    ephemeral and rebuilt from S3 at the start of every turn (S3 = source of truth)."""

    #: Owned by workspace_sync -- publish-right-after-write (claude_driver's PostToolUse
    #: hook) and this batch sync must upload **the same set**. With two copies, a file
    #: present in only one shows up as "a document that appears and then disappears".
    _SYNC_GLOBS = SYNC_GLOBS
    _RESTORE_PREFIXES = ("aiplc-docs/", "prototype/", "uploads/")
    #: Subtrees restored byte-for-byte. `uploads/` holds original images and PDFs --
    #: going through the text path (UTF-8 decode) corrupts them into U+FFFD. They are
    #: input written only by the upload route and never uploaded to the source of truth
    #: (outside SYNC_GLOBS), so no hash is recorded either.
    _BYTE_PREFIXES = ("uploads/",)

    def __init__(self, project_id, driver, s3: S3StoreLike, local_root: Path, session: dict):
        self.project_id = project_id
        self._driver = driver
        self._s3 = s3
        self._local_root = Path(local_root)
        self._session = session
        self._turn_active = False
        #: Identity of the turn holding the slot. Only the holder may release it -- the
        #: driver follows the same discipline (ClaudeDriver._acquire_turn/_release_turn).
        self._turn_token: object | None = None
        self._pending_interrupt_id: str | None = None
        #: The turn's last context usage. This is the value the screen shows when no
        #: client is connected (after a backend restart) (aipds/context_usage.py).
        self._context = ContextRecord(s3, DISCOVERY_KEY)
        self._remote_etags: dict[str, str | None] | None = None
        self._synced_hashes: dict[str, str] = {}
        set_callback = getattr(driver, "set_file_published_callback", None)
        if set_callback is not None:
            set_callback(self._record_published_file)

    # ---- file-as-contract ops: directly on durable S3 ----

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

    async def write_bytes_if_absent(self, rel_path: str, content: bytes) -> bool:
        """`write_file_if_absent` for an upload's original (an image, a PDF)."""
        reject_unsafe(rel_path)
        return await self._s3.put_bytes_if_absent(rel_path, content)

    async def list_files(self, glob: str) -> list[str]:
        reject_unsafe(glob)
        keys = await self._s3.list(_glob_prefix(glob))
        return sorted(k for k in keys if matches_glob(k, glob))

    async def list_files_newest_first(self, glob: str) -> list[str]:
        """Same glob as `list_files`, sorted by last-modified time, newest first.

        Uses the `LastModified` that `list_objects_v2` already returns
        (S3Store.list_with_times) -- no extra calls. Callers that need alphabetical
        order keep using `list_files`.
        """
        reject_unsafe(glob)
        pairs = await self._s3.list_with_times(_glob_prefix(glob))
        matched = [(k, t) for k, t in pairs if matches_glob(k, glob)]
        # Path is the secondary key -- so entries with the same time keep a stable order across runs.
        matched.sort(key=lambda item: (-item[1], item[0]))
        return [k for k, _ in matched]

    # ---- workspace <-> S3 ----

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
        """Copy the durable workspace (S3 = source of truth) to the local FS.
        The first turn fetches everything; later turns fetch only files whose ETag changed."""
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
            reject_unsafe(key)
            unlink_beneath(self._local_root, key)
            self._synced_hashes.pop(key, None)

        keys = [
            key for key, etag in remote.items()
            if cold
            or etag is None
            or previous.get(key) != etag
            or stat_beneath(self._local_root, key) is None
        ]
        bodies = await asyncio.gather(*(
            self._s3.get_bytes(key) if key.startswith(self._BYTE_PREFIXES)
            else self._s3.get(key)
            for key in keys))
        # The first turn writes the whole workspace -- disk writes happen off the loop.
        hashes = await asyncio.to_thread(
            self._write_restored, list(zip(keys, bodies)))
        self._synced_hashes.update(hashes)
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

    def _write_restored(
        self, items: list[tuple[str, str | bytes]],
    ) -> dict[str, str]:
        """Write restored bodies locally and return per-key hashes. Synchronous -- runs in a thread.
        Byte bodies (`_BYTE_PREFIXES`) are written as-is and get no hash.

        Hashes are only returned, not put into `_synced_hashes` directly: that dict is also
        touched on the loop side (publish-right-after-write), so it is updated on the loop
        in one go.

        Links are not followed (`pathsafe.write_beneath`). The agent writes to this tree, so
        following them would write source-of-truth bodies, as the backend uid, wherever the
        agent chose.
        """
        hashes: dict[str, str] = {}
        if items:
            self._local_root.mkdir(parents=True, exist_ok=True)
        for key, body in items:
            write_beneath(self._local_root, key, body)
            if isinstance(body, str):
                hashes[key] = _content_hash(body)
        return hashes

    def _scan_sync_candidates(self) -> list[tuple[str, str, str]]:
        """Candidates to upload to the source of truth: (key, content to upload, hash).
        Synchronous -- runs in a thread.

        It reads and hashes every target file, so running it on the loop would stall other
        projects' streams by that much at the end of every turn.

        Only the top-level directories of `_SYNC_GLOBS` are walked. No other key can match
        any glob, so the answer is the same, and the workspace root also holds the rules
        copied in every turn plus CLI state. If a glob with a wildcard in its first segment
        is ever added, the whole root is walked.

        **Symbolic links are not followed.** `rglob` from the root does not follow directory
        links, but it does follow one given as the starting point -- so a top-level directory
        that is a link is not walked. File links are returned by `rglob`, so they are skipped,
        and reads also go through `read_text_beneath` (a link swapped in after the walk but
        before the read). If the agent places a link pointing outside, the outside file would
        be uploaded to the source of truth.
        """
        tops = {glob.split("/", 1)[0] for glob in self._SYNC_GLOBS}
        if any(ch in top for top in tops for ch in "*?["):
            roots = [self._local_root]
        else:
            roots = [self._local_root / top for top in sorted(tops)]
        found: list[tuple[str, str, str]] = []
        for root in roots:
            if root.is_symlink() or not root.is_dir():
                continue
            for path in root.rglob("*"):
                if path.is_symlink() or not path.is_file():
                    continue
                key = path.relative_to(self._local_root).as_posix()
                if not any(matches_glob(key, glob) for glob in self._SYNC_GLOBS):
                    continue
                reject_unsafe(key)
                try:
                    text = read_text_beneath(self._local_root, key, errors="replace")
                except OSError:
                    continue  # became a link or disappeared during the walk
                content = content_for_s3(key, text)
                found.append((key, content, _content_hash(content)))
        return found

    async def _sync_workspace_to_s3(self) -> None:
        """Lift turn output (methodology artifacts + prototype source subtree) from local to
        durable S3. audit.md is redacted on save (blocks exposure to direct S3 readers)."""
        started = time.perf_counter()
        found = await asyncio.to_thread(self._scan_sync_candidates)
        scanned = len(found)
        changed: list[tuple[str, str]] = [
            (key, content) for key, content, digest in found
            if self._synced_hashes.get(key) != digest]

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

    def _claim_turn(self) -> object | None:
        """Claim the turn slot. None if it is already held.

        A token is issued so that only the holder can release it -- `_finish_turn` checks
        the token, so a rejected call cannot free the slot of the turn that is running.
        """
        if self._turn_active:
            return None
        self._turn_active = True
        self._turn_token = object()
        return self._turn_token

    def _finish_turn(self, token: object | None) -> None:
        """Release the slot if this token is still the holder. Otherwise a no-op."""
        if self._turn_token is token:
            self._turn_active = False
            self._turn_token = None

    async def _record_user_input(self, record: UserInput | None) -> None:
        """Record the user input that opened the turn in audit.md verbatim, with the real time (audit_log header).

        **After the restore, before the agent.** The restore overwrites the local audit.md
        with the source-of-truth copy, so writing earlier would be lost, and it must be written
        before the agent calls its first tool so the agent appends its own entries after it.
        It is uploaded to the source of truth right away too -- even if the turn is cut off
        midway, what the user entered must remain.
        """
        if record is None:
            return
        key = audit_log.append(self._local_root, record)
        await publish_file(self._s3, self._local_root, key,
                           on_published=self._record_published_file)

    async def send_message(self, text: str,
                           record: UserInput | None = None) -> AsyncIterator[AgentEvent]:
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
            await self._record_user_input(record)
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
                self._context.observe(event)
                if event.kind in ("done", "error"):
                    # Sync BEFORE yielding the terminal event: a client that
                    # reads a doc the moment it sees `done` must not race the
                    # upload (fail-closed — a sync error surfaces instead of
                    # the terminal event).
                    await self._sync_workspace_to_s3()
                    synced = True
                    await self._context.flush()
                yield event
        finally:
            self._finish_turn(token)
            # Backstop for every path that never reached the terminal event:
            # the turn job being cancelled (project delete, backend shutdown —
            # aipds/turn_job.py) or the driver raising mid-turn. Without
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

    async def send_answers(self, answers: dict[str, str],
                           record: UserInput | None = None) -> AsyncIterator[AgentEvent]:
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
            await self._record_user_input(record)
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
                self._context.observe(event)
                if event.kind in ("done", "error"):
                    await self._sync_workspace_to_s3()
                    synced = True
                    await self._context.flush()
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
        """Cut the in-progress turn. Delegates to the driver.

        Does not touch the turn slot -- the running run() releases it itself when it emits
        the terminal event. Releasing it here as well would be a double release.
        """
        interrupt = getattr(self._driver, "interrupt", None)
        if interrupt is None:
            return  # an optional method outside the contract -- no-op if absent (still idempotent)
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
        """Clean up the local workspace + shut down the driver. Does not touch S3 (durable)
        -- deletion is handled by delete_project_data in projects.py.

        The driver's disconnect() is an **optional** method outside the contract
        (run/run_answers/pending). ClaudeDriver holds a claude subprocess, so without this
        call, every project deletion would leak that process for the whole backend
        lifetime (~300-500MB). getattr only checks whether it exists and silently skips
        it if not -- this guard stays even now that there is only one driver, so as not
        to mix the contract (the three methods runner.py uses) with implementation
        convenience."""
        disconnect = getattr(self._driver, "disconnect", None)
        if disconnect is not None:
            try:
                await disconnect()
            except Exception:
                _log.exception("driver disconnect failed for %s", self.project_id)
        await asyncio.to_thread(shutil.rmtree, self._local_root, ignore_errors=True)


def _glob_prefix(glob: str) -> str:
    """The leading static (wildcard-free) directory part of a glob = the S3 list prefix.
    'aiplc-docs/**/*-q.md' -> 'aiplc-docs/', 'aiplc-docs/audit.md' -> itself."""
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
