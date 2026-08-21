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
from typing import AsyncIterator, Callable, Literal, Protocol, TYPE_CHECKING

from aipds.models import AgentEvent
from aipds.proto import prompts
from aipds.proto.design_sync import sync_design
from aipds.s3store import S3StoreLike
from aipds.proto import layout

if TYPE_CHECKING:
    # A deferred import for type hints only. design_profile.py does not use session.py so
    # there is no cycle, but this module has no reason to carry a DesignProfileStore as a
    # value -- the session receives the store opaquely and only calls load().
    from aipds.design_profile import DesignProfileStore

_log = logging.getLogger(__name__)

SessionStatus = Literal["starting", "ready", "building", "waiting_input",
                        # The state where the agent declared completion through
                        # build_complete. Why it differs from "ready": ready means "another
                        # turn can be accepted" and complete means "this session has
                        # finished its work". _DEAD_STATUSES in routes/prototypes.py depends
                        # on that distinction.
                        "complete",
                        "failed", "closed"]

#: The three opening prompts first_prompt() chooses between.
#:   plan    -- from scratch. Plan only, do not build.
#:   resume  -- pick up a session that died without declaring completion (the whole
#:              transcript).
#:   handoff -- improve a completed build (a new session plus the summary only).
PromptKind = Literal["plan", "resume", "handoff"]


def has_build_output(build_dir: Path) -> bool:
    """Whether there is output in `prototype/` under the build directory.

    The single definition of "it has been built". Three places ask this question and all of
    them have to come through here -- `first_prompt()` (what to instruct), the
    `build_complete` tool (whether to accept a completion declaration), and the list route
    (whether to show the card as built). If the criteria diverge, you get a state where the
    tool accepts completion but the list does not show built (or the other way round).

    Looking at `prototype/` rather than at the build directory itself is the point.
    `start()` plants the spec .md before the agent runs, and a previous hosting attempt can
    leave `.proto-host.log` and `.pid` behind -- so the build directory existing means only
    that the session started, not that something was produced.

    It checks direct children without recursing: that keeps every list call cheap even after
    node_modules and .next appear.
    """
    proto_dir = build_dir / "prototype"
    try:
        return proto_dir.is_dir() and any(proto_dir.iterdir())
    except OSError:
        return False


class BuilderLike(Protocol):
    def run(self, text: str) -> AsyncIterator[AgentEvent]: ...
    async def submit_answers(self, interrupt_id: str,
                             answers: dict[str, str]) -> bool: ...
    async def interrupt(self) -> None: ...
    async def pending(self) -> str | None: ...
    async def disconnect(self) -> None: ...


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


#: The grace period between a completion declaration and the session closing itself. It is
#: not 0 because the terminal event needs room to make its way out of the generator chain
#: (_relay_queue -> run -> send_message -> gen).
_COMPLETION_GRACE_SECONDS = 5


def _completion_from(payload: str | None) -> dict | None:
    """A build_complete payload -> {"summary","remaining"} or None.

    The same fail-soft discipline as _interrupt_id_from -- a broken payload is demoted to
    None rather than raising. If the completion handling does not happen, the idle timer
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
    return {"summary": summary,
            "remaining": remaining if isinstance(remaining, str) else ""}


def _is_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


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
        # This project's output language. It selects the opening prompt and the
        # build_complete tool text (proto/prompts.py).
        self._language = language
        self._idle_seconds = idle_seconds
        # The profile store lives outside the project (at the bucket root), so it is a
        # different store from self._s3. With None it runs without a brand -- the whole
        # feature is opt-in.
        self._design_profiles = design_profiles

        self.status: SessionStatus = "starting"
        self._builder: BuilderLike | None = None
        self._session_id: str | None = None
        # Which kind of prompt first_prompt() will choose. It replaces the former
        # `_resumed` boolean -- with three branches, a boolean cannot express it.
        self._prompt_kind: PromptKind = "plan"
        # What to carry in the prompt on the handoff branch ({"summary","remaining"}).
        self._handoff: dict | None = None
        self._pending_interrupt_id: str | None = None
        # The completion declaration's content ({"summary","remaining"}) or None. It means
        # two things at once: (1) this session has finished its work, and (2) the idle timer
        # should use the short grace period (see _arm_idle_timer).
        self._completion: dict | None = None
        self._idle_handle: asyncio.TimerHandle | None = None
        self._closed = False
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
        return f"prototypes/{self.slug}/session.json"

    def _handoff_key(self) -> str:
        return f"prototypes/{self.slug}/handoff.json"

    def build_dir(self) -> Path:
        return self._build_root / self.project_id / self.slug

    # ---- durable session id ----

    async def _resolve_session_id(self) -> tuple[str, bool, PromptKind]:
        """Return (session_id, resume, prompt_kind).

        There are three branches, each expressing a different event:

          nothing stored            -> a new id, no resume, "plan"
          stored, no handoff        -> resume the stored id, "resume"
          stored plus a handoff     -> a new id, no resume, "handoff"

        The third is the point of this design. Carrying the whole transcript into an
        improvement of a completed build loads the entire build context onto a request to
        change one button colour. Carry only the summary and start afresh.

        Why the second remains: a session that died **without** declaring completion (an
        idle timeout, a backend restart) really is a resume. That session did not finish its
        work, and the context to pick up cannot be replaced by a summary.

        A stored value that is not a UUID is treated as absent -- the SDK rejects a non-UUID
        resume, so this keeps a legacy or hand-edited value from blocking the session
        forever.
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

        # An improvement session: switch to a new id and consume the handoff.
        #
        # The order matters -- write session.json first, delete the handoff second. A
        # failure in between leaves the handoff, so the next start takes this branch again;
        # session.json already holds the new (empty) id, so it starts afresh with the
        # improvement prompt: the same outcome. The reverse order, if the id write fails
        # after the handoff is deleted, loses the summary and resumes the old session in
        # full. Avoid the lossy direction.
        self._handoff = handoff
        new_id = str(uuid.uuid4())
        await self._s3.put(self._session_key(),
                           json.dumps({"session_id": new_id}))
        # delete_prefix is used to delete a single key -- S3StoreLike has no single-key
        # delete, and this is the established convention (agent/pending_store.py:69,
        # survey/store.py:334).
        await self._s3.delete_prefix(self._handoff_key())
        return new_id, False, "handoff"

    async def _read_handoff(self) -> dict | None:
        """handoff.json -> {"summary","remaining"} or None.

        The same fail-soft discipline as _completion_from. A broken handoff must not block
        the improvement path -- demoted to None it falls to the second branch (a full
        resume), which is a heavy but accurate degradation.
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
        """Re-arm the idle timer. The delay is decided here rather than by the caller -- that
        is the easiest part of this design to get wrong.

        Had the caller passed it as an argument, the done that follows a completion
        declaration armed on the short grace period would put it back to the default 30
        minutes and the session would never close. done **necessarily** follows
        build_complete (the terminal-held discipline in run()), so this is not a possibility
        but a certainty. Deriving the delay from state means that window does not exist.
        """
        delay = (_COMPLETION_GRACE_SECONDS if self._completion is not None
                 else self._idle_seconds)
        if self._idle_handle is not None:
            self._idle_handle.cancel()
        loop = asyncio.get_running_loop()
        self._idle_handle = loop.call_later(delay, self._on_idle_timeout)

    def _on_idle_timeout(self) -> None:
        asyncio.create_task(self.close())

    async def _write_handoff(self, completion: dict) -> None:
        """The handoff the next session will read. It is the only thing that lets an
        improvement avoid carrying the whole transcript (the third branch of
        _resolve_session_id).

        completed_at is for diagnostics -- it makes the branch taken readable from the
        log.
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
        spec_path = build_dir / self._spec_key()
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(spec_md, encoding="utf-8")

        # Apply the brand profile to the workspace. Rewritten on every start for the same
        # reason as the spec -- a value an admin edited takes effect from this session on.
        #
        # fail-soft: DesignProfileStore.load() already demotes every S3 exception to None,
        # but it is wrapped once more here -- if that promise breaks for any reason, a
        # failure to apply the brand must not block the build session from starting at all.
        # This pairs with the hosting route (start_host in routes/prototypes.py) wrapping
        # the same spot in the same try/except -- if the two callers diverge on this
        # discipline, only one of them is vulnerable to a brand error.
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

    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        assert self._builder is not None, "start() must be called before send_message()"
        # A session that has declared completion accepts no new turn. Today
        # _DEAD_STATUSES in routes/prototypes.py blocks it with a 404 before this call, but
        # this object must not depend on a caller to protect it -- with a route bypass (a
        # test, some future entry point) the turn relay below would run as usual, move
        # status back to "building" and stamp over the completed state.
        #
        # Guarding on self._completion rather than self.status is for the same reason as
        # everywhere else in this module -- _completion is the fact of the completion
        # declaration itself, and nothing but this field ever unsets it. status is a mutable
        # value several paths rewrite (this very method's turn relay, for one), so using it
        # as a guard for this purpose would let the guard be defeated by the exact
        # assignment it is trying to prevent.
        #
        # Why it ends with a yield rather than a raise: the `except Exception` at the end of
        # this method turns a mid-turn failure into a "failed" session and releases the
        # build slot -- that path means "this session cannot be used any more". A completed
        # session is the opposite: a normal end with all its work done, and its slot is
        # already in the reclaim procedure on the short grace period from the completion
        # handling. Raising here would reclassify a normal end as a failure and drive it
        # towards a double release (or releasing someone else's slot). So it yields a
        # turn-level error in the same shape as the builder's error event and simply returns
        # -- the caller (the SSE generator) receives it exactly like any error turn, and the
        # session state stays "complete".
        if self._completion is not None:
            yield AgentEvent(
                kind="error",
                text=prompts.session_already_complete(self._language),
            )
            return
        self._arm_idle_timer()
        self.status = "building"
        try:
            async for event in self._builder.run(text):
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
                        # `except Exception` below catches it and goes to status="failed"
                        # plus a slot release, which is the opposite of the decision that
                        # "completion proceeds even if the handoff fails". An S3 failure
                        # must not make a finished build look like a failed one.
                        try:
                            await self._write_handoff(completion)
                        except Exception:
                            _log.exception("handoff write failed: %s/%s",
                                           self.project_id, self.slug)
                        # Re-arm on the grace period. Passing no argument is the point --
                        # the delay is derived by _arm_idle_timer from self._completion, so
                        # this call reads the completion state just set and picks the short
                        # grace period. Why it goes after the handoff write: it avoids the
                        # race where the grace timer expires while the write is still in
                        # flight and close() cuts in. Without this call the default idle
                        # timer armed on entry to send_message stays in place and the
                        # session does not close for 30 minutes (measured: 2 grace-period
                        # tests failing).
                        self._arm_idle_timer()
                elif event.kind in ("done", "error"):
                    # A session that has declared completion does not go back to ready.
                    # done necessarily follows build_complete, so without this guard status
                    # would revert and defeat the whole _DEAD_STATUSES mechanism (hosting a
                    # 409 again, and no way to open an improvement session).
                    #
                    # Why error is bundled in: if an error arrives after a completion
                    # declaration, that is no grounds for making this session ready either.
                    # Before completion it stays retryable as before.
                    if self._completion is None:
                        self.status = "ready"
                # A liveness signal. This is where the timer's meaning changes from "since
                # the turn started" to "since the last event". Before this, a build turn
                # longer than 30 minutes died mid-flight, and 30 minutes spent with a
                # question card on screen made the answer submission a 409.
                #
                # It does not undo the completion grace period -- _arm_idle_timer derives
                # the delay from self._completion, so a done after completion keeps the
                # short grace period too.
                #
                # Cost: a TimerHandle.cancel() plus call_later pair happens thousands of
                # times in one build. Both are a single heap operation so the real cost is
                # nil, but it is worth knowing that this is called per event.
                self._arm_idle_timer()
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

    async def send_answers(self, answers: dict[str, str]) -> bool:
        assert self._builder is not None, "start() must be called before send_answers()"
        if self._pending_interrupt_id is None:
            return False
        interrupt_id, self._pending_interrupt_id = self._pending_interrupt_id, None
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

        # A prior mid-turn failure in send_message already released this
        # session's slot -- releasing again would free a slot that belongs to
        # some OTHER session (the semaphore's clamp-at-zero only guards
        # against going negative, not against crediting the wrong holder).
        if not self._slot_released:
            self._slot_released = True
            self._semaphore.release()
        self.status = "closed" if ok else "failed"

    # ---- first turn's auto-spoken prompt ----

    def first_prompt(self) -> str:
        """The opening turn, spoken automatically. Three shapes, chosen by `_prompt_kind`.

        All three end the same way -- AskUserQuestion, and then waiting. That tool is the
        only one whose permission callback we intercept, which makes asking a question also
        the way to stop the turn and put the options up in the UI
        (builder._on_can_use_tool -> a `questions` SSE event). And this wording is the only
        brake: the builder runs under bypassPermissions so Write and Edit are auto-approved,
        leaving no way outside this text to stop an agent that simply starts building.

        plan    -> plan only, do not build yet.
        resume  -> the transcript and the half-built files are already in context. Do not
                   re-plan; ask what to continue with.
        handoff -> the build is finished and the only context is a summary. Ask what to
                   improve.
        """
        if self._prompt_kind == "handoff" and self._handoff is not None:
            return self._handoff_prompt(self._handoff)
        if self._prompt_kind == "resume":
            return self._resume_prompt()
        return self._plan_prompt()

    def _plan_prompt(self) -> str:
        """The sentences themselves are held per language by proto/prompts.py."""
        return prompts.plan_prompt(
            self._language,
            spec_key=self._spec_key(),
            proxy_path=f"/api/proto/{self.project_id}/{self.slug}/")

    def _resume_prompt(self) -> str:
        """Deliberately short. The agent already has the prior transcript and
        whatever it built, so restating the spec or the build rules would only
        compete with what it can already see. All this turn has to do is stop
        it from picking a direction on its own.

        Unless the build tree is GONE, which the transcript cannot tell it --
        see `_missing_output_prompt`.

        The sentences themselves are held per language by proto/prompts.py.
        """
        if not has_build_output(self.build_dir()):
            return self._missing_output_prompt()
        return prompts.resume_prompt(self._language)

    def _missing_output_prompt(self) -> str:
        """The opening turn after the output has disappeared -- it says rebuild, do not search.

        The resume and handoff prompts both presume the code the agent produced is still
        there. Two paths break that presumption and both are normal operation: a prototype
        reset (which rmtrees the local tree) and a hosting instance replacement (the build
        tree is on EBS and only the S3 session survives).

        Not told about that state, the agent trusts the transcript and goes looking for code
        that is not there. Measured: on a reset prototype it burned over 19 seconds widening
        its search from the working directory to another prototype's directory to
        `/opt/aipds/frontend` to the whole filesystem -- a search that could not succeed,
        because the tree was deleted.

        Having it read the spec again is the point. The transcript's memory is a conversation
        record rather than a summary, and code cannot be recovered from it. The spec is alive
        in S3 and `start()` plants it locally afresh every time -- the same input as the
        first build.

        The sentences themselves are held per language by proto/prompts.py.
        """
        return prompts.missing_output_prompt(self._language,
                                             spec_key=self._spec_key())

    def _handoff_prompt(self, handoff: dict) -> str:
        """The opening turn of a new session improving a completed build.

        Shorter even than `_resume_prompt`. Not passing a file tree is deliberate -- the
        agent reading the cwd with its own file tools is more accurate than a snapshot, and
        that is already how it reads the spec. All this has to do is say what the previous
        build left behind and stop it from making changes on its own initiative.

        But there may in fact be nothing left. handoff.json is in S3 while the build tree is
        on local disk -- if the instance is replaced, only the summary survives. Saying "the
        build is already complete" and having it look over a `prototype/` that is not there
        is exactly what triggers that search (see `_missing_output_prompt`).

        The sentences themselves are held per language by proto/prompts.py.
        """
        if not has_build_output(self.build_dir()):
            return self._missing_output_prompt()
        return prompts.handoff_prompt(
            self._language,
            spec_key=self._spec_key(),
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
    """
    await s3.delete_prefix(f"prototypes/{slug}/")
