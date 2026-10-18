# backend/aipds/proto/session.py — PrototypeSession: one prototype build
# session's orchestration.
#
# Post-MicroVM shape: no boot, no HTTP file push, no VM stop. What remains is
# (1) resolving the durable session id so context resumes, (2) making sure the
# build directory and the spec exist on local disk for the agent's own file
# tools, (3) relaying turns, (4) the idle timer -- which now reclaims a ~300-
# 500MB subprocess and a build slot rather than a VM.
#
# Closing a session no longer destroys context: the transcript lives in S3 and
# the build directory stays on disk, so the next start() resumes.
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Callable, Iterable, Literal, Protocol, TYPE_CHECKING

from aipds.context_usage import ContextRecord
from aipds.proto.history import context_key
from aipds.models import AgentEvent
from aipds.turn_job import TurnJobs
from aipds.proto.store import (PrototypeStore, has_build_output,  # noqa: F401 — re-export
                               mark_completed, output_digest)
from aipds.proto import prompts
from aipds.proto.build_guard import user_url_hosts
from aipds.proto.design_sync import sync_design
from aipds.proto.hosting import restore_local
from aipds.pathsafe import unlink_beneath, write_beneath
from aipds.s3store import S3StoreLike
from aipds.proto import layout

if TYPE_CHECKING:
    # Deferred import for type hints only. design_profile.py does not use session.py, so
    # this is not a cycle, but this module has no reason to carry DesignProfileStore
    # around as a value -- the session receives the store opaquely and only calls
    # load().
    from aipds.design_profile import DesignProfileStore

_log = logging.getLogger(__name__)

SessionStatus = Literal["starting", "ready", "building", "waiting_input",
                        # The agent has declared completion via build_complete. Why this
                        # differs from "ready": ready means "can take another turn",
                        # while complete means "this session has finished its work".
                        # _DEAD_STATUSES in routes/prototypes.py depends on this
                        # distinction.
                        "complete",
                        "failed", "closed"]

#: The three opening prompts that first_prompt() chooses between.
#:   plan    -- From scratch. Only make a plan; do not build.
#:   resume  -- Take over a session that died without a completion (full transcript).
#:   handoff -- Improve a completed build (new session + summary only).
PromptKind = Literal["plan", "resume", "handoff"]


class BuilderLike(Protocol):
    def run(self, text: str) -> AsyncIterator[AgentEvent]: ...
    async def submit_answers(self, interrupt_id: str,
                             answers: dict[str, str]) -> bool: ...
    async def interrupt(self) -> None: ...
    async def pending(self) -> str | None: ...
    async def disconnect(self) -> None: ...
    def allow_fetch_hosts(self, hosts: Iterable[str]) -> None: ...


class SemaphoreLike(Protocol):
    def try_acquire(self) -> bool: ...
    def release(self) -> None: ...
    def snapshot(self) -> dict[str, int]: ...


def _interrupt_id_from(payload: str | None) -> str | None:
    """Parse the interrupt id out of a questions payload. Mirrors runner.py --
    a malformed/contract-drifted payload must degrade (None) rather than blow
    up the turn relay."""
    if not payload:
        return None
    try:
        value = json.loads(payload).get("interrupt_id")
    except (json.JSONDecodeError, AttributeError):
        return None
    return value if isinstance(value, str) else None


#: Grace period after a completion declaration before the session closes itself. Why it
#: is not 0: the terminal event needs room to make its way out of the generator chain
#: (_relay_queue -> run -> send_message -> gen).
_COMPLETION_GRACE_SECONDS = 5


def _completion_from(payload: str | None) -> dict | None:
    """build_complete payload -> {"summary","remaining"} or None.

    Same fail-soft discipline as _interrupt_id_from -- a broken payload is downgraded to
    None rather than raising. If completion handling does not happen, the idle timer
    cleans up as usual, which is the safer direction than a wrongly declared completion.
    """
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    summary = data.get("summary")
    if not isinstance(summary, str) or not summary:
        return None
    remaining = data.get("remaining")
    changes = data.get("changes")
    return {"summary": summary,
            "remaining": remaining if isinstance(remaining, str) else "",
            "changes": changes if isinstance(changes, str) else ""}


def _is_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def session_key(slug: str) -> str:
    """This prototype's SDK session id (durable). Why it is a module function: some
    places outside the session also ask "which conversation is current"
    (proto/history.load_context)."""
    return f"prototypes/{slug}/session.json"


def handoff_key(slug: str) -> str:
    """The summary left by a completed build. If present, the next start() is a new
    conversation (_resolve_session_id)."""
    return f"prototypes/{slug}/handoff.json"


class PrototypeSession:
    """One prototype's build session: owns the durable session id, the build
    directory, the turn relay, the questions interrupt id, and the idle timer.
    """

    def __init__(
        self,
        project_id: str,
        slug: str,
        s3: S3StoreLike,
        build_root: Path,
        builder_factory: Callable[[str, bool], BuilderLike],
        semaphore: SemaphoreLike,
        language: str = "ko",
        idle_seconds: int | float = 1800,
        design_profiles: "DesignProfileStore | None" = None,
    ):
        self.project_id = project_id
        self.slug = slug
        self._s3 = s3
        self._build_root = Path(build_root)
        self._builder_factory = builder_factory
        self._semaphore = semaphore
        # This project's output language. The opening prompt and the build_complete tool
        # text are chosen by this value (proto/prompts.py).
        self._language = language
        self._idle_seconds = idle_seconds
        # The profile store lives outside the project (at the bucket root), so it is a
        # different store from self._s3. If None, the build runs without a brand -- the
        # whole feature is opt-in.
        self._design_profiles = design_profiles

        self.status: SessionStatus = "starting"
        self._builder: BuilderLike | None = None
        self._session_id: str | None = None
        # The kind of prompt first_prompt() will pick. Replaces the former `_resumed`
        # boolean -- there are now three branches, which a boolean cannot express.
        self._prompt_kind: PromptKind = "plan"
        # Whether the opening turn has already run. **This must be a fact the session
        # knows, not a UI flag**: it is what the route uses to decide whether to wrap
        # the first message in the opening prompt, and if the frontend guesses, it
        # drifts on refresh, races, and sessions that are already open (see the request
        # description in `first_prompt`).
        self._opened = False
        # What to put in the prompt on the handoff branch ({"summary","remaining"}).
        self._handoff: dict | None = None
        # Whether the handoff that was read has been deleted. If not on the handoff
        # branch there is nothing to delete, so True.
        self._handoff_consumed = True
        # Key of the build instructions this start() planted in cwd. None means there
        # are none (Path B, or Discovery has not reached Step 3 yet). The opening prompt
        # points to this.
        self._instructions_key: str | None = None
        # Key of the change history this start() planted in cwd. None means there is
        # none (first build, or after a reset).
        self._history_key: str | None = None
        self._pending_interrupt_id: str | None = None
        # The completion declaration ({"summary","remaining"}) or None. It means two things
        # at once: (1) this session has finished its work, (2) the idle timer
        # must use the short grace period (see _arm_idle_timer).
        self._completion: dict | None = None
        self._idle_handle: asyncio.TimerHandle | None = None
        self._closed = False
        #: This session's turn jobs. Turns belong to the session, not the request
        #: (aipds/turn_job.py header) -- even if the build screen is refreshed the turn
        #: runs to the end, and a reopened screen replays this session's turns from the
        #: start to restore the conversation. Hence there is no retention limit: they
        #: disappear together when the session closes (30 min idle, completion grace
        #: period).
        self.turns = TurnJobs(retention=float("inf"))
        #: The source of truth for the build source (S3). The session restores it on
        #: start and snapshots it on completion/close.
        self._store = PrototypeStore(s3, project_id=project_id)
        #: The turn's last context usage (aipds/context_usage.py). This is the value the
        #: panel shows after the session closes -- it carries the SDK session id with it
        #: so stale values are filtered out in an improvement session.
        self._context = ContextRecord(s3, context_key(slug))
        # A mid-turn raise releases the slot immediately in send_message's
        # except below (nothing else would -- the caller sees the exception
        # and abandons the session without ever calling close()). This flag
        # is the guard against a LATER close() or idle-timeout releasing the
        # same slot a second time, which would wrongly free a slot some
        # OTHER session is holding (BuildSemaphore.release() clamps at 0, so
        # it can't detect an over-release itself).
        self._slot_released = False

    # ---- path/key helpers ----

    def _spec_key(self) -> str:
        return layout.spec_key(self.slug)

    def _session_key(self) -> str:
        return session_key(self.slug)

    def _handoff_key(self) -> str:
        return handoff_key(self.slug)

    def build_dir(self) -> Path:
        return self._build_root / self.project_id / self.slug

    # ---- durable session id ----

    async def _resolve_session_id(self) -> tuple[str, bool, PromptKind]:
        """Return (session_id, resume, prompt_kind).

        There are three branches, each representing a different event:

          nothing stored              -> new id, no resume, "plan"
          stored, no handoff          -> resume the stored id, "resume"
          stored + handoff            -> new id, no resume, "handoff"

        The third is the point of this design. If improving a completed build carried the
        whole transcript along, even a request to change one button color would load the
        entire build context. Instead we load only the summary and start fresh.

        Why the second remains: a session that died **without** a completion declaration
        (idle timeout, backend restart) is still correctly a true resume. That session
        did not finish its work, and the context to carry over cannot be replaced by a
        summary.

        A non-UUID stored value is treated as absent -- the SDK rejects a non-UUID
        resume, so a legacy/hand-edited value must not block the session forever.
        """
        try:
            saved = json.loads(await self._s3.get(self._session_key()))
        except (FileNotFoundError, json.JSONDecodeError):
            saved = None

        if not (isinstance(saved, dict) and _is_uuid(saved.get("session_id"))):
            new_id = str(uuid.uuid4())
            await self._s3.put(self._session_key(),
                               json.dumps({"session_id": new_id}))
            return new_id, False, "plan"

        handoff = await self._read_handoff()
        if handoff is None:
            return saved["session_id"], True, "resume"

        # Improvement session: switch to a new id and **leave** the handoff in place.
        #
        # Do not delete it here. The new id only materializes as a transcript when the
        # CLI receives its first message; if the session closes between start() and that
        # turn (the screen navigated away and came back, reconnect right after a
        # deploy), the stored id has no substance and the handoff is already gone. The
        # next start takes the "stored, no handoff" = resume branch, --resumes a
        # conversation that does not exist ("No conversation found"), and that state is
        # permanent until manual recovery. So consumption happens after the turn has
        # actually started (`_consume_handoff`). If it dies before then, the next start
        # simply takes this branch again and uses another new id -- one extra empty id
        # is the entire loss.
        self._handoff = handoff
        self._handoff_consumed = False
        new_id = str(uuid.uuid4())
        await self._s3.put(self._session_key(),
                           json.dumps({"session_id": new_id}))
        return new_id, False, "handoff"

    async def _consume_handoff(self) -> None:
        """The improvement session's first turn has started, so delete the handoff (see
        `_resolve_session_id`).

        Failures are swallowed. If the handoff remains, the next start just takes the
        handoff branch again, which is a direction that loses the transcript, not one
        that blocks the session. It is not called after a completion declaration has
        written a new handoff -- consumption happens on the first event, which is
        earlier.

        Uses delete_prefix to delete a single key -- S3StoreLike has no single-key
        delete, and this is the established convention (agent/pending_store.py:69,
        survey/store.py:334).
        """
        self._handoff_consumed = True
        try:
            await self._s3.delete_prefix(self._handoff_key())
        except Exception:
            _log.exception("handoff consume failed: %s/%s",
                           self.project_id, self.slug)

    async def _read_handoff(self) -> dict | None:
        """handoff.json -> {"summary","remaining"} or None.

        Same fail-soft discipline as _completion_from. A broken handoff must not block
        the improvement path -- downgraded to None, it falls through to the second
        branch (full resume), which is a heavy but correct degradation.
        """
        try:
            data = json.loads(await self._s3.get(self._handoff_key()))
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        summary = data.get("summary")
        if not isinstance(summary, str) or not summary:
            return None
        remaining = data.get("remaining")
        return {"summary": summary,
                "remaining": remaining if isinstance(remaining, str) else ""}

    # ---- idle timer ----

    def _arm_idle_timer(self) -> None:
        """Re-arm the idle timer. The delay value is decided here, not by the
        caller -- that is the easiest part of this design to get wrong.

        If callers passed it as an argument, then right after a completion declaration
        armed the short grace period, the following done would reset it to the default
        30 minutes and the session would not close. Since done **always** follows
        build_complete (run()'s terminal held discipline), this is a certain behavior,
        not a possibility. Deriving the delay from state means that window does not
        exist.
        """
        delay = (_COMPLETION_GRACE_SECONDS if self._completion is not None
                 else self._idle_seconds)
        if self._idle_handle is not None:
            self._idle_handle.cancel()
        loop = asyncio.get_running_loop()
        self._idle_handle = loop.call_later(delay, self._on_idle_timeout)

    def _on_idle_timeout(self) -> None:
        asyncio.create_task(self.close())

    async def _snapshot_quietly(self, reason: str) -> int | None:
        """Upload the build source as an S3 generation (proto/store.py). Failure does
        not block the build -- the local tree stays as is, and the next snapshot point
        (hosting, close) tries again. Returns the uploaded generation number (None on
        failure)."""
        try:
            return await self._store.snapshot(self.slug, self.build_dir(), reason)
        except Exception:
            _log.exception("prototype source snapshot failed: %s/%s (%s)",
                           self.project_id, self.slug, reason)
            return None

    async def _append_change_history(self, completion: dict,
                                     gen: int | None) -> None:
        """Append the completion declaration's `changes` to the change history.

        Unlike handoff.json, this is **not deleted after consumption** -- handoff is a
        one-time summary of the previous build and disappears once the next session
        reads it, but this file holds every change since the spec, and it is the only
        basis by which the Nth improvement session can know the changes of the (N-1)th.

        S3 has no append write, so we read, concatenate, and write. A prototype has only one
        build session at a time (upstream semaphore and session map), so there is no race.
        """
        try:
            existing = await self._s3.get(layout.change_history_key(self.slug))
        except FileNotFoundError:
            existing = prompts.change_history_header(self._language)
        entry = prompts.change_history_entry(
            self._language,
            at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            gen=gen,
            summary=completion["summary"],
            changes=completion.get("changes") or "")
        await self._s3.put(layout.change_history_key(self.slug), existing + entry)

    async def _write_handoff(self, completion: dict) -> None:
        """The handoff the next session will read. It is the only reason an improvement task
        does not have to carry the full transcript (the third branch of
        _resolve_session_id).

        completed_at is for diagnostics -- it lets you read from the logs which branch
        was taken.
        """
        await self._s3.put(self._handoff_key(), json.dumps({
            **completion,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False))

    # ---- start ----

    async def start(self) -> None:
        spec_md = await self._s3.get(self._spec_key())  # FileNotFoundError -> route 404

        self._session_id, resume, self._prompt_kind = await self._resolve_session_id()

        # The agent reads the spec with its own file tools from cwd, so it has
        # to exist on local disk (the VM era pushed it over HTTP instead).
        # Refreshed on every start so a spec edited in Discovery is picked up.
        build_dir = self.build_dir()
        # If there is no build output locally, restore it from the current S3 generation
        # (proto/store.py). If an improvement session opened after the instance was
        # replaced does not know the previous build and rebuilds from the spec, it
        # produces a different thing, not the prototype that was surveyed. The opening
        # prompt branches on "has it been built" (`first_prompt`), so this restore must
        # come before that.
        try:
            await restore_local(self.project_id, self.slug, build_dir, self._store)
        except Exception:
            _log.exception("prototype source restore failed: %s/%s",
                           self.project_id, self.slug)
        # This is the tree the build agent writes to -- planted files do not follow
        # links (pathsafe).
        build_dir.mkdir(parents=True, exist_ok=True)
        write_beneath(build_dir, self._spec_key(), spec_md)

        # The improvement session re-plants, from the current tree, the baseline for "do
        # not accept completion if nothing has changed since the last completion"
        # (proto/tools.py). A handoff existing means the last session declared
        # completion and no session has changed the tree since, so the current tree is
        # exactly that baseline. This way, prototypes completed before the marker
        # existed also get the gate from their first improvement session.
        if self._prompt_kind == "handoff" and has_build_output(build_dir):
            try:
                mark_completed(build_dir,
                               await asyncio.to_thread(output_digest, build_dir))
            except Exception:
                _log.exception("completion marker seed failed: %s/%s",
                               self.project_id, self.slug)

        # Path A.1 leaves build instructions next to the spec -- the builder input
        # defined upstream, combining the spec and the design-context answers
        # (layout.BUILD_INSTRUCTIONS). If only the spec were planted, the design
        # direction and device context the user chose in Step 2 would not reach the
        # builder. For the same reason as the spec, it is rewritten on every start, and
        # if it has disappeared from S3 the local copy is also deleted -- if left
        # behind, stale instructions sit in cwd that the prompt does not mention.
        instructions_key = layout.build_instructions_key(self.slug)
        try:
            instructions_md = await self._s3.get(instructions_key)
        except FileNotFoundError:
            unlink_beneath(build_dir, instructions_key)
            self._instructions_key = None
        else:
            write_beneath(build_dir, instructions_key, instructions_md)
            self._instructions_key = instructions_key

        # The change history is also planted next to the spec. The spec is frozen at
        # approval time and later changes exist only in this file -- improvement
        # sessions and rebuilds must read it so they do not revive features the spec has
        # already dropped. For the same reason as the instructions, it is rewritten on
        # every start, and deleted if absent.
        history_key = layout.change_history_key(self.slug)
        try:
            history_md = await self._s3.get(layout.change_history_key(self.slug))
        except FileNotFoundError:
            unlink_beneath(build_dir, history_key)
            self._history_key = None
        else:
            write_beneath(build_dir, history_key, history_md)
            self._history_key = history_key

        # Apply the brand profile to the workspace. For the same reason as spec, it is
        # rewritten on every start -- values an admin fixed take effect from this session.
        #
        # fail-soft: DesignProfileStore.load() already downgrades every S3 exception to
        # None, but we wrap it once more here -- even if that promise breaks for any
        # reason, a brand application failure must not block the build session from
        # starting. This pairs with the hosting route (start_host in
        # routes/prototypes.py) wrapping the same spot with the same try/except -- if
        # the two call sites diverge on this discipline, only one of them becomes
        # vulnerable to brand errors.
        try:
            profile = (await self._design_profiles.load()
                       if self._design_profiles is not None else None)
            sync_design(build_dir, profile, self._language)
        except Exception:
            _log.exception("design sync at session start failed: %s/%s",
                           self.project_id, self.slug)

        self._builder = self._builder_factory(self._session_id, resume)
        self.status = "ready"
        self._arm_idle_timer()

    # ---- turn relay ----

    @property
    def opened(self) -> bool:
        """Whether the opening turn has already run. If False, the next message is the
        opening turn.

        The route uses this value to decide whether to wrap the user's first message in
        the opening prompt (the request description in `first_prompt`). Being read-only
        is the point -- the only place that can change this fact should be
        `send_message`.
        """
        return self._opened

    async def send_message(self, text: str, *,
                           user_text: str | None = None) -> AsyncIterator[AgentEvent]:
        """`text` is what goes to the agent; `user_text` is what the user actually wrote.

        They differ on the opening turn (the route wraps the first message in the
        opening prompt). `user_text` comes separately because of WebFetch permission --
        only URLs the user wrote may be opened, and the wrapped prompt also contains
        URLs from the model-written handoff summary.
        """
        assert self._builder is not None, "start() must be called before send_message()"
        # A session that has declared completion does not accept new turns. Today
        # _DEAD_STATUSES in routes/prototypes.py blocks this call with a 404 beforehand,
        # but this object must not depend on callers to protect it -- if there is a
        # route bypass (tests, a future different entry point), the turn relay below
        # would run as usual, set status back to "building", and trample the completed
        # state.
        #
        # The reason to guard on self._completion rather than self.status is the same as
        # everywhere else in this module -- _completion is the fact of the completion
        # declaration itself, and nothing besides this field ever reverts it. status is
        # a mutable value that several paths (e.g., the turn relay in this very method)
        # rewrite, so using it as a guard for the same purpose would let this guard be
        # defeated by the very assignment it is meant to protect against.
        #
        # Why it ends with yield rather than raise: the `except Exception` at the end of
        # this method turns a mid-turn failure into a "failed" session and releases the
        # build slot -- that is, that path means "this session can no longer be used". A
        # completed session is the exact opposite: it is a normal end with all its work
        # done, and its slot already entered the reclamation procedure with the short
        # grace period at completion handling. Raising here would reclassify a normal
        # end as a failure and push the slot toward a double release (or releasing
        # someone else's slot) attempt. So it yields a turn-level error shaped like the
        # builder's error event and simply returns -- the caller (the SSE generator)
        # receives it exactly like an ordinary error turn, and the session status stays
        # "complete".
        if self._completion is not None:
            yield AgentEvent(
                kind="error",
                text=prompts.session_already_complete(self._language),
            )
            return
        self._allow_user_urls(user_text)
        self._arm_idle_timer()
        # This message consumes the opening turn. Setting it **after** passing the
        # completion guard (above) is intentional: the error turn a completed session
        # sends back is not an opening turn.
        self._opened = True
        self.status = "building"
        try:
            async for event in self._builder.run(text):
                # The agent connected and the turn has started running -- the new id's
                # transcript now comes into existence. If an error is the first event,
                # it never even connected, so do not consume.
                if not self._handoff_consumed and event.kind != "error":
                    await self._consume_handoff()
                if event.kind == "questions":
                    got = _interrupt_id_from(event.payload)
                    if got:
                        self._pending_interrupt_id = got
                        self.status = "waiting_input"
                elif event.kind == "build_complete":
                    completion = _completion_from(event.payload)
                    if completion is not None:
                        self._completion = completion
                        self.status = "complete"
                        # The exception must be swallowed. Otherwise the
                        # `except Exception` below catches it and goes to
                        # status="failed" + slot release, which is the exact opposite of
                        # the decision that "completion proceeds even if the handoff
                        # fails". An S3 failure must not make a finished build look
                        # failed.
                        try:
                            await self._write_handoff(completion)
                        except Exception:
                            _log.exception("handoff write failed: %s/%s",
                                           self.project_id, self.slug)
                        gen = await self._snapshot_quietly("build_complete")
                        # Same discipline as handoff -- a history write failure must not
                        # make a finished build look failed.
                        try:
                            await self._append_change_history(completion, gen)
                        except Exception:
                            _log.exception("change history write failed: %s/%s",
                                           self.project_id, self.slug)
                        # Re-arm with the grace period. Passing no argument is the point
                        # -- _arm_idle_timer derives the delay from self._completion, so
                        # this call reads the completion state just set and picks the
                        # short grace period. Why it comes after the handoff write: it
                        # avoids the race where the grace timer expires first while the
                        # write is in progress and close() cuts in. Without this call,
                        # the default idle timer armed on entering send_message would
                        # remain and the session would not close for 30 minutes
                        # (measured: 2 grace-period tests failed).
                        self._arm_idle_timer()
                elif event.kind in ("done", "error"):
                    await self._context.flush(session_id=self._session_id)
                    # A session that has declared completion does not go back to ready.
                    # Since done always follows build_complete, without this guard
                    # status would revert and the entire _DEAD_STATUSES mechanism would
                    # be defeated (hosting would 409 again, and an improvement session
                    # could not be opened).
                    #
                    # Why error is grouped in too: if an error arrives after the
                    # completion declaration, that is not grounds to make this session
                    # ready either. Before completion, it stays retryable as before.
                    if self._completion is None:
                        self.status = "ready"
                # Liveness signal. This is where the timer's meaning changes from "since
                # turn entry" to "since the last event". Previously, a build turn longer
                # than 30 minutes died mid-run, and if a question card was shown for 30
                # minutes, submitting the answer returned 409.
                #
                # This does not revert the completion grace period -- _arm_idle_timer
                # derives the delay from self._completion, so a done after completion
                # also keeps the short grace period.
                #
                # Cost: a TimerHandle.cancel() + call_later pair happens thousands of
                # times per build. Each is a single heap operation, so the real cost is
                # nil, but be aware that it is called on every event.
                self._arm_idle_timer()
                self._context.observe(event)
                yield event
        except Exception:
            self.status = "failed"
            # The caller (routes/prototypes.py) sees this exception propagate
            # out of the SSE generator and never gets a session to close --
            # the retry path evicts the dict entry outright. Without
            # releasing here, the slot is gone until process restart.
            if not self._slot_released:
                self._slot_released = True
                self._semaphore.release()
            raise

    def _allow_user_urls(self, text: str | None) -> None:
        hosts = user_url_hosts(text)
        if hosts:
            assert self._builder is not None
            self._builder.allow_fetch_hosts(hosts)

    async def send_answers(self, answers: dict[str, str]) -> bool:
        assert self._builder is not None, "start() must be called before send_answers()"
        if self._pending_interrupt_id is None:
            return False
        interrupt_id, self._pending_interrupt_id = self._pending_interrupt_id, None
        # The "기타" (Other) field of the question card is also text the user wrote
        # themselves.
        self._allow_user_urls("\n".join(answers.values()))
        # Record the answer in the turn log **first**. The build turn continues past the
        # question without breaking, so the answer is an event inside the turn -- it
        # must be recorded so that a reattached screen restores the answer bubble after
        # the question card and closes the card. If it were recorded after waking the
        # builder, events the agent emitted in between could be recorded before the
        # answer.
        job = self.turns.current()
        if job is not None:
            job.log.append(AgentEvent(kind="answers", payload=json.dumps(
                {"answers": answers}, ensure_ascii=False)))
        ok = await self._builder.submit_answers(interrupt_id, answers)
        if not ok:
            return False
        self._arm_idle_timer()
        self.status = "building"
        return True

    async def interrupt(self) -> None:
        if self._builder is not None:
            await self._builder.interrupt()

    # ---- close: disconnect + release the slot. Context is NOT discarded. ----

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        if self._idle_handle is not None:
            self._idle_handle.cancel()
            self._idle_handle = None

        ok = True
        builder_gone = self._builder is None
        if self._builder is not None:
            try:
                await self._builder.disconnect()
            except Exception:
                # A wedged subprocess must not keep the build slot forever --
                # log it, mark the session failed, and still release below.
                _log.exception("builder disconnect failed: %s/%s",
                               self.project_id, self.slug)
                ok = False
            self._builder = None

        # A running turn ends on its own once the builder goes down -- wait briefly to
        # let it close via the normal path, and cancel it if it is still running. Why
        # not cancel immediately: when closing due to the completion grace period, the
        # turn has only the `done` after build_complete left, and cancelling would
        # attach "중단됨" (Interrupted) after the completion declaration.
        job = self.turns.current()
        if job is not None and job.task is not None and not builder_gone:
            try:
                await asyncio.wait_for(asyncio.shield(job.task), timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass
        await self.turns.cancel()
        # A build that ended without a completion declaration (idle close, user closed
        # it) is not lost either. If the content is the same as the current generation,
        # no new generation is created.
        await self._snapshot_quietly("session_close")

        # A prior mid-turn failure in send_message already released this
        # session's slot -- releasing again would free a slot that belongs to
        # some OTHER session (the semaphore's clamp-at-zero only guards
        # against going negative, not against crediting the wrong holder).
        if not self._slot_released:
            self._slot_released = True
            self._semaphore.release()
        self.status = "closed" if ok else "failed"

    # ---- first turn's auto-spoken prompt ----

    def first_prompt(self, request: str | None = None) -> str:
        """The automatically fired opening turn. It has three shapes, chosen by
        `_prompt_kind`.

        All three end the same way -- AskUserQuestion, then wait. That tool is the only
        one whose permission callback we intercept, so asking a question is also how the
        turn is paused and the choices are put in the UI (builder._on_can_use_tool ->
        `questions` SSE event). And this wording is the only brake: the builder runs
        with bypassPermissions, so Write/Edit are auto-approved, and there is no way
        outside this text to stop an agent that simply starts building.

        plan    -> Only make a plan; do not build yet.
        resume  -> The transcript and half-built files are already in context. Do not
                   re-plan; ask what to continue.
        handoff -> The build is done and the only context is the summary. Ask what to
        improve.

        `request` is **the request the user already made as the session's first
        message**. If present, resume/handoff skip asking back and do it right away --
        the rationale is in proto/prompts.handoff_prompt (while asking back the input
        box was disabled, so the user could not even type their own request). The
        context (summary, "look at prototype/ first") goes along even when there is a
        request: handoff is a new session, so there is no other way to know about the
        previous build.
        """
        if self._prompt_kind == "handoff" and self._handoff is not None:
            return self._handoff_prompt(self._handoff, request=request)
        if self._prompt_kind == "resume":
            return self._resume_prompt(request=request)
        return self._plan_prompt(request=request)

    def _plan_prompt(self, request: str | None = None) -> str:
        """The wording itself lives in proto/prompts.py, per language."""
        return prompts.plan_prompt(
            self._language,
            request=request,
            spec_key=self._spec_key(),
            instructions_key=self._instructions_key,
            proxy_path=f"/api/proto/{self.project_id}/{self.slug}/")

    def _resume_prompt(self, request: str | None = None) -> str:
        """Deliberately short. The agent already has the prior transcript and
        whatever it built, so restating the spec or the build rules would only
        compete with what it can already see. All this turn has to do is stop
        it from picking a direction on its own.

        Unless the build tree is GONE, which the transcript cannot tell it --
        see `_missing_output_prompt`.

        The wording itself lives in proto/prompts.py, per language.
        """
        if not has_build_output(self.build_dir()):
            return self._missing_output_prompt(request)
        return prompts.resume_prompt(self._language, request=request)

    def _missing_output_prompt(self, request: str | None = None) -> str:
        """The opening turn after the output has disappeared -- tells the agent to
        rebuild rather than search.

        The resume and improvement prompts both assume the code the agent built is still
        there. There are two paths that break that assumption, and both are normal
        operation: a prototype reset (rmtree of the local tree) and hosting instance
        replacement (the build tree is on EBS and only the S3 session survives).

        If the state is not stated then, the agent trusts the transcript and goes
        looking for code that does not exist. Measured: on a reset prototype it widened
        the search from the working directory -> another prototype directory ->
        `/opt/aipds/frontend` -> the entire filesystem, burning more than 19 seconds on
        a search that could not succeed -- the tree had been deleted.

        Making it reread the spec is the point. What the transcript remembers is a
        conversation record, not a summary, and code cannot be reconstructed from it.
        The spec lives on in S3 and `start()` plants it locally fresh every time -- the
        same input as the first build.

        The wording itself lives in proto/prompts.py, per language.

        **`request` does not remove the asking back.** Something that does not exist
        cannot be fixed, so the work here is a rebuild, and that approval round trip
        must remain. Still, the words the user typed are appended -- if silently
        dropped, that request would remain nowhere, and the user would have no way to
        know their words were ignored.
        """
        return prompts.missing_output_prompt(
            self._language, spec_key=self._spec_key(),
            instructions_key=self._instructions_key,
            history_key=self._history_key, request=request)

    def _handoff_prompt(self, handoff: dict, *,
                        request: str | None = None) -> str:
        """The opening turn of a new session that improves a completed build.

        Even shorter than `_resume_prompt`. Not passing the file tree is deliberate
        -- the agent reading cwd with its own file tools is more accurate than a snapshot,
        and that is already how it reads the spec. All this has to do is tell it what the
        previous build left behind and keep it from touching things on its own.

        However, there may actually be nothing left behind. handoff.json is in S3 and
        the build tree is on local disk -- so when the instance is replaced only the
        summary survives. Saying "the build is already complete" while having it look at
        a nonexistent `prototype/` is exactly what triggers that search (see
        `_missing_output_prompt`).

        The wording itself lives in proto/prompts.py, per language.
        """
        if not has_build_output(self.build_dir()):
            return self._missing_output_prompt(request)
        return prompts.handoff_prompt(
            self._language,
            spec_key=self._spec_key(),
            instructions_key=self._instructions_key,
            history_key=self._history_key,
            request=request,
            summary=handoff["summary"],
            remaining=handoff.get("remaining")
            or prompts.missing_remaining_note(self._language))


async def purge_session_state(s3, slug: str) -> None:
    """Delete the S3 state this module owns for one prototype: the durable
    session id, the build transcript, and the legacy bundle/ backup.

    A module function, not a method: once a build finishes the session is
    evicted from `proto_sessions` (the normal resting state), so anything
    hanging off an instance could not reach the very prototypes that most need
    resetting.

    Scoped to `prototypes/{slug}/` and therefore never touches the spec, which
    lives under aiplc-docs/ -- deleting that would remove the card from the
    list instead of resetting it. Idempotent: absent keys are a no-op.

    Callers MUST run SurveyStore.purge() BEFORE this: the survey tree lives
    under this same prefix, and reclaiming its token indexes requires reading
    the questionnaires that this call would delete.

    The change history is the one exception to that scope: it lives next to the
    spec (layout.change_history_key) so Discovery can read it, and it describes
    the build this reset throws away -- a history of changes to a build that no
    longer exists would have the next build assume them. Exact key, for the same
    reason as SurveyStore.purge: the directory also holds the spec.
    """
    await s3.delete_prefix(f"prototypes/{slug}/")
    await s3.delete_prefix(layout.change_history_key(slug))
