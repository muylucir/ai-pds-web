# backend/pathfinder/agent/claude_driver.py — Discovery agent driver on the
# Claude Agent SDK, running IN-PROCESS (no VM). Same three-method contract as
# StrandsDriver (driver.py) so runner.py and the frontend cannot tell them
# apart -- proven by tests/driver_contract.py's assert_driver_contract, which
# both drivers pass.
#
# Most of the SDK plumbing below (client construction, can_use_tool
# interception, PostToolUse hook, event translation, the queue-polling race
# while a question is pending, --session-id/--resume conflict avoidance) is
# COPIED from pathfinder/proto/builder.py -- the prototype build driver that
# already solved this exact problem and runs in production. The comments
# documenting *why* each piece exists are carried across unchanged because
# they record findings from real failures; duplicating them here (rather than
# extracting a shared module) is a deliberate, human-ruled choice: the
# boundary between the two drivers isn't known yet, and this task's job is to
# get ClaudeDriver working and passing the contract, not to design that
# abstraction prematurely.
#
# ONE STRUCTURAL DIFFERENCE FROM builder.py, and it is the whole reason this
# file is not a copy-paste: what a pending question does to the turn.
#
#   builder.py keeps ONE SSE stream open across the question round trip. Its
#   `run()` polls forever, and proto/session.py's `send_answers` is not a
#   stream at all -- it just resolves the future (returns bool), after which
#   the SAME `run()` generator keeps yielding.
#
#   Discovery's contract is the opposite: runner.py exposes `send_answers` as
#   its OWN async iterator (runner.py:154-179), and the frontend refuses to
#   submit answers while a stream is still open
#   (frontend/lib/useWorkspaceStream.ts:230, `if (stopRef.current) return`).
#   So `run()` MUST terminate when a question is raised -- yield `questions`,
#   then `done`, and return -- or the user can never answer. runner.py needs
#   that terminal event for a second reason: it syncs the workspace to S3 only
#   on done/error (runner.py:134-140), so a turn parked mid-question would
#   leave everything the agent already wrote in the VOLATILE local workspace.
#
#   `_pump` below therefore stops on a queued `questions` event, leaving the
#   SDK's can_use_tool callback suspended on its future, and `run_answers`
#   resolves that future and relays the REST of the same turn (never a second
#   `query()`).
#
#   What makes that round trip safe is `_MessageReader`: a task that owns the
#   turn's `receive_response()` iterator, keeps reading across the whole
#   question round trip, and collects into a plain inbox on the driver. So the
#   messages the CLI sends while the question is on screen -- or while the SSE
#   client is disconnected -- are held by US, not by anyio, and the answers turn
#   relays them. Read `_MessageReader`'s docstring before editing anything in
#   this area: the alternative shape (peek future + cancel) is unfixable, and it
#   cost three review rounds one lost-message defect each.
#
# The other genuinely new piece is pending-question persistence across a
# backend restart (see _on_can_use_tool/pending/_resume_with_answers) --
# builder.py has no analog because a prototype build's "pending" is purely
# in-memory (PrototypeSession owns its own idle-timeout/close lifecycle);
# Discovery's GET /pending must survive both a page refresh (same process) and
# a backend redeploy (different process), so the payload is mirrored to S3.
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path, PurePosixPath
from typing import Any, AsyncIterator, Callable

from pathfinder.agent.pending_store import clear_pending, load_pending, save_pending
from pathfinder.agent.questions_payload import question_file_from_sdk
from pathfinder.agent.tools import build_tools
from pathfinder.agent.workspace_rules import place_rules
from pathfinder.models import AgentEvent
from pathfinder.s3store import S3StoreLike

_log = logging.getLogger("pathfinder.agent")

_FILE_TOOLS = {"Write", "Edit", "MultiEdit"}
_LETTERS = "ABCDEFGHIJ"

# How long `_pump` blocks on the next SDK message before checking the
# callback/hook queue again. Copied from builder.py:319 -- see `_pump`'s
# docstring for why the poll exists at all.
_POLL_SECONDS = 0.05

# Discovery runs with a human in the loop watching the chat, unlike the
# unattended prototype build -- but AskUserQuestion is still routed through
# can_use_tool (see _on_can_use_tool below) and every other tool must execute
# without a separate approval round-trip, since the UI's only approval
# mechanism IS the questions card. Kept as the same default as builder.py's
# DEFAULT_PERMISSION_MODE for the same reason: any mode that can prompt stalls
# the turn with no operator to answer the CLI-level prompt.
DEFAULT_PERMISSION_MODE = "bypassPermissions"

_MCP_SERVER_NAME = "pathfinder"

# Mirrors StrandsDriver's B1 short-circuit (driver.py:166-177). Same wording,
# because the frontend renders it as an ordinary AI message either way and the
# two drivers must be indistinguishable.
_ANSWER_FIRST = ("진행 중인 질문에 먼저 답변해 주세요 — 우측 패널의 질문 폼을 "
                 "이용하세요.")


class _MessageReader:
    """Owns ONE `receive_response()` iterator and drains it into an inbox.

    This class exists to make a whole bug class unrepresentable, so its
    rationale is worth stating in full.

    `_pump` has to race two sources: the SDK's next message, and the
    hook/tool-callback queue (while an AskUserQuestion is parked the CLI is
    blocked on the permission response and `receive_response()` yields nothing
    at all, so a plain `async for` would never let the queued `questions` event
    reach the SSE stream). The obvious way to race them is a *peek future* over
    `agen.__anext__()`, polled on a short timeout — which is what this driver
    did for three review rounds, and it lost messages in every one of them.

    Why the peek future cannot be made safe, probed on a real anyio
    memory-object stream (the SDK owns exactly such a stream —
    claude_agent_sdk/_internal/query.py:121):

      - `send_nowait` hands an item DIRECTLY to a parked receiver and does not
        buffer it (anyio/streams/memory.py:220-231). So the item's only copy
        lives in that receiver.
      - Cancelling the peek future therefore DESTROYS the item; it is not left
        in the buffer for a later iterator. Measured: `(parked, buffered)`
        goes `(1, 0) -> (0, 0)` on the send, and a fresh iterator then times
        out.
      - Every `await` and every `yield` between arming the peek and cancelling
        it is a window where that can happen. Each round closed one window and
        left (or opened) another: the queue drain, the second sweep, `yield
        done`, the pre-terminal drain, the abandoned exit.
      - "Cancel before suspending, re-arm afterwards" cannot fix it either:
        cancelling `agen.__anext__()` CLOSES the async generator. Probed —
        re-arming the same iterator after a cancel raises StopAsyncIteration,
        and the item is only recoverable through a brand-new iterator. So the
        peek future has no safe suspension protocol at all.

    The fix is to stop racing the message at all. A dedicated task owns the
    iterator and never stops reading; `_pump` only ever consumes from
    `inbox`, a plain list on the driver. Then:

      - The pump owns no cancellable RECEIVE, so no suspension of the pump can
        destroy a message in flight from the SDK. There is no window to
        enumerate.
      - Unread messages live in OUR inbox, not in anyio's buffer, so they
        survive the consumer abandoning the generator (SSE disconnect, proxy
        timeout, navigation — runner.py:144-152 takes that path routinely) and
        are picked up by the answers turn. Probed: a reader nobody cancels
        receives every item sent after the consumer walked away.
      - Each message is popped exactly once, so double translation (and any
        side effect a message carries) is impossible by construction.

    The only cancellable wait left is `settle()`'s, and it waits on an
    `asyncio.Event`, which carries no payload — cancelling it cannot lose
    anything. That is the whole structural difference.

    NOTE what this does NOT buy on its own, because assuming otherwise was the
    round-4 regression: moving ownership here protects a message only while it
    is IN `inbox`/`outbox`. An event copied out into a local list before being
    yielded is back to living in a frame `GeneratorExit` destroys. `_pump`'s
    invariant 2 states the ownership rule that closes that half.

    The reader is cancelled only when the turn it belongs to is being
    REPLACED (`_retire_reader`) or the subprocess is going away
    (`disconnect`); never while the turn may still continue.
    """

    def __init__(self, agen) -> None:
        self._agen = agen
        self.inbox: list = []
        # Translated events not yet delivered to the consumer. This is their
        # DURABLE HOME -- see `_relay`'s ownership rule. It lives on the reader
        # so its lifetime is exactly the turn's: `_retire_reader` disposing of
        # the reader disposes of the undelivered events of the turn nobody will
        # relay, and a new turn starts with an empty one.
        self.outbox: list[AgentEvent] = []
        self.ended = False
        self.error: BaseException | None = None
        # Woken on every append so the pump reacts immediately instead of
        # waiting out its poll interval; sticky, cleared by settle().
        self.wake = asyncio.Event()
        self.task: asyncio.Task = asyncio.ensure_future(self._drain())

    async def _drain(self) -> None:
        try:
            async for msg in self._agen:
                self.inbox.append(msg)
                self.wake.set()
        except asyncio.CancelledError:
            # Deliberate teardown (see _retire_reader/disconnect) -- must not
            # be recorded as a turn failure.
            raise
        except Exception as e:
            # Captured rather than left on the task, so nothing logs
            # "Task exception was never retrieved" and `_pump` can raise it on
            # the caller's stack, where `_stream` degrades it to the contract's
            # "agent turn failed".
            self.error = e
        finally:
            self.ended = True
            self.wake.set()

    async def settle(self, timeout: float) -> None:
        """Wait for something new, or `timeout`.

        The ONE cancellable await `_pump` owns. It waits on an Event, which
        carries no item, so a cancellation here cannot destroy a message --
        unlike awaiting the message future itself.

        No lock and no re-check after `clear()`: one event loop, and `_drain`
        appends to `inbox` BEFORE it sets the event, so the only ordering that
        could lose a wakeup (clear -> append -> set) cannot happen without an
        await between the check and the clear, and there is none. A stale
        wakeup is harmless anyway -- `_pump` just harvests an empty inbox and
        loops.
        """
        if self.inbox or self.ended:
            return
        self.wake.clear()
        try:
            await asyncio.wait_for(self.wake.wait(), timeout)
        except TimeoutError:  # asyncio.TimeoutError is TimeoutError on 3.11
            pass


def _iid_of(event: AgentEvent) -> str | None:
    """The interrupt_id inside a `questions` event's payload, or None."""
    if not event.payload:
        return None
    try:
        value = json.loads(event.payload).get("interrupt_id")
    except (json.JSONDecodeError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def _rel(path: str, workspace: str) -> str | None:
    """Make a tool's file_path workspace-relative; reject escapes.

    Ported from proto/builder.py's `_rel` (itself ported from the VM-era
    claude_driver._rel). `relative_to` also raises ValueError when `path` is
    absolute but shares no prefix with `workspace` at all (e.g. "/etc/passwd"
    vs workspace "/workspace") -- not just for genuinely relative inputs. A
    naive fallback (`path.lstrip("/")`) would treat both cases as "already
    relative", letting an unrelated absolute path escape undetected. Only fall
    back to the lstrip path when `path` was not absolute to begin with; an
    absolute path that isn't under the workspace is always an escape.
    """
    ws = PurePosixPath(workspace)
    p = PurePosixPath(path)
    try:
        rel = p.relative_to(ws)
    except ValueError:
        if path.startswith("/"):
            return None
        rel = PurePosixPath(path.lstrip("/"))
    rel_str = str(rel)
    if ".." in rel.parts or rel_str.startswith("/"):
        return None
    return rel_str


def _validate_permission_mode(mode: str) -> str:
    """Reject an unknown mode here rather than letting it reach the CLI.

    An unrecognized --permission-mode kills the subprocess during connect(),
    which run() reports as a generic "agent turn failed" -- the exact
    late-and-opaque failure mode the --session-id/--resume clash produced in
    the prototype builder. The valid set is read off the SDK's own Literal so
    it cannot drift.
    """
    from typing import get_args

    from claude_agent_sdk.types import PermissionMode

    valid = get_args(PermissionMode)
    if mode not in valid:
        raise ValueError(
            f"unknown permission_mode {mode!r}; expected one of {', '.join(valid)}")
    return mode


def _sdk_session_id(session: dict) -> tuple[str, bool]:
    """(session_id, resume) for ClaudeAgentOptions -- a UUID, always.

    The CLI rejects a non-UUID outright: probed against the bundled binary,
    `claude --session-id=pilot1 -p hi` prints "Error: Invalid session ID. Must
    be a valid UUID." and exits 1. That kills the subprocess during connect(),
    which run() can only report as a generic "agent turn failed" -- i.e. every
    turn of the workshop dies opaquely.

    And Discovery's session id is NOT a UUID today: app.py:299-304 sets
    `session_id = project_id`, which is free-form user input (routes/
    projects.py's CreateProject does not validate it), so a project named
    "pilot1" is a 100% failure. proto/session.py:124-138 already solved this
    for the builder -- mint a UUID and treat a non-UUID value as "start
    fresh" -- so the same judgment is carried over here rather than trusting
    the caller.

    Deriving the UUID from the project id (uuid5) rather than uuid4 keeps it
    STABLE across restarts, which is what makes `--resume` able to find the
    transcript at all; a fresh random id every process would silently start a
    new conversation each redeploy. A value that is already a UUID is passed
    through untouched, so a caller that does the right thing is not overridden.

    The `resume` flag this returns is the CALLER's request, not the decision.
    Stability alone is what makes `--session-id` collide (see
    `_transcript_exists`), so `_ensure_client` overrides this flag from the
    transcript on disk; `_resume_with_answers` still passes resume=True to
    express intent, and a caller asking to resume a session that has no
    transcript must not be taken at its word either.
    """
    import uuid

    raw = session.get("session_id") or ""
    resume = bool(session.get("resume"))
    try:
        return str(uuid.UUID(str(raw))), resume
    except (ValueError, AttributeError, TypeError):
        pass
    if not raw:
        # Nothing to derive from: a random id is still better than a value the
        # CLI will reject, but there is no transcript to resume.
        return str(uuid.uuid4()), False
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"pathfinder:{raw}")), resume


def _transcript_path(config_dir: str, workspace: str, session_id: str) -> Path:
    """Where the CLI keeps this (cwd, session_id) pair's transcript.

    `<config_dir>/projects/<encoded cwd>/<session_id>.jsonl`, where the
    encoding replaces every character outside [A-Za-z0-9-] with "-". Both the
    layout and the encoding are measured against the bundled binary (2.1.220),
    not guessed -- eight cwds were probed, including spaces, dots, "+", "=",
    underscores and Hangul, and `re.sub(r"[^A-Za-z0-9-]", "-", cwd)` reproduced
    every directory name the CLI created. The cwd is resolved first because the
    CLI encodes the REAL path: with cwd a symlink to /tmp/c1probe/realws the
    transcript landed under `-tmp-c1probe-realws`, not under the link name.

    Note the encoding is lossy (Hangul project ids all collapse to runs of
    "-"), so two different workspaces could in principle share a directory.
    That is the CLI's own namespace and this function's job is only to look in
    the same place it does; a collision would make this probe say "resume" for
    a session the CLI can also resume, which is consistent either way. The
    session id itself is per-project (uuid5 of the project id), so the FILE
    within that directory is still distinct.
    """
    try:
        cwd = str(Path(workspace).resolve())
    except OSError:  # pragma: no cover -- unresolvable cwd; use it verbatim
        cwd = workspace
    return (Path(config_dir) / "projects"
            / re.sub(r"[^A-Za-z0-9-]", "-", cwd) / f"{session_id}.jsonl")


def _transcript_exists(config_dir: str, workspace: str, session_id: str) -> bool:
    """Whether the CLI already has a conversation under this id and cwd.

    This is THE decision `--session-id` vs `--resume` turns on, and it is not a
    heuristic: the CLI's "already in use" check IS this file's existence.
    Probed against 2.1.220 -- after `claude --session-id=<id>` succeeded once,
    a second run with the same id in the same cwd exits 1 with "Session ID
    <id> is already in use."; moving that one .jsonl aside made the SAME id
    succeed again. And the two errors are exact complements, so neither flag is
    safe unconditionally:

        --session-id=<id> with a transcript   -> exit 1 "already in use"
        --resume=<id>     with no transcript  -> exit 1 "No conversation found"

    Either one kills the subprocess inside connect(), which run() can only
    report as "agent turn failed" -- permanently, since the transcript file is
    durable, and a poisoned client cache used to make it permanent per project
    as well (see _ensure_client). The failure needs no restart to trigger:
    disconnect() on DELETE /projects/acme followed by re-creating `acme` in the
    same process hits it on the second turn.

    A filesystem probe rather than a flag we carry ourselves, because the CLI's
    own state is the only thing that can answer it. Anything we persisted
    separately (S3, a driver attribute) would be a second source of truth that
    drifts the moment the config dir is recycled, the instance is replaced, or
    a deploy wipes /opt/pathfinder -- and the drift is silent in both
    directions. proto/session.py:124-138 makes the same "resume only if the
    saved id is real" call for the builder; the difference is only WHERE the
    evidence lives (it owns its S3 record, we read the CLI's disk).

    Racy in principle (the file could appear between this check and connect),
    but the writer is the CLI subprocess we are about to start, and this
    driver is single-turn (`_acquire_turn`), so nothing else in the process is
    starting a session for this workspace concurrently.
    """
    try:
        return _transcript_path(config_dir, workspace, session_id).is_file()
    except OSError:  # pragma: no cover -- an unreadable config dir
        _log.exception("transcript probe failed — starting a fresh session")
        return False


def _default_client_factory(driver: "ClaudeDriver") -> Callable[[dict], Any]:
    def make(session: dict):
        from claude_agent_sdk import (
            ClaudeAgentOptions, ClaudeSDKClient, create_sdk_mcp_server,
        )
        from claude_agent_sdk.types import HookMatcher

        env = {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            # Swap the config HOME rather than disabling settings entirely
            # (setting_sources=[]): the bundled binary is ordinary Claude Code
            # and reads ~/.claude when this is unset, which on the workshop
            # EC2 is the operator's personal skills/agents/CLAUDE.md. Beyond
            # that leak, Discovery needs its OWN config dir rather than the
            # prototype builder's proto-config: sharing would leave the
            # prototype's shadcn-design skill instruction active while
            # Discovery writes documents (discovery-config/README.md).
            "CLAUDE_CONFIG_DIR": driver._config_dir,
        }
        if driver._anthropic_model:
            env["ANTHROPIC_MODEL"] = driver._anthropic_model
        session_id, resume = _sdk_session_id(session)
        # Task 5 left create_sdk_mcp_server to the caller so build_tools could
        # keep a plain `-> list[SdkMcpTool]` contract. allowed_tools' entries
        # MUST be spelled "mcp__<server key>__<tool name>" -- the SDK builds
        # that name itself when it serializes --mcp-config, so any other
        # spelling silently leaves the tool needing approval.
        server = create_sdk_mcp_server(
            name=_MCP_SERVER_NAME,
            tools=build_tools(driver._workspace, driver._emit))
        options = ClaudeAgentOptions(
            permission_mode=driver._permission_mode,
            cwd=driver._workspace,
            env=env,
            # "user" now means OUR config dir (discovery-config/), so this is
            # safe -- and it is what lets the CLAUDE.md there be discovered.
            setting_sources=["user", "project"],
            # `skills` intentionally NOT set: unlike the prototype builder
            # (skills="all", for shadcn-design), Discovery's upstream AI-PLC
            # setup has no skills of its own -- it is CLAUDE.md plus on-demand
            # rule-file reads (discovery-config/README.md).
            mcp_servers={_MCP_SERVER_NAME: server},
            allowed_tools=[f"mcp__{_MCP_SERVER_NAME}__report_stage",
                           f"mcp__{_MCP_SERVER_NAME}__submit_document"],
            # Exactly one of the two, never both: the CLI rejects
            # `--session-id` alongside `--resume` unless `--fork-session` is
            # also passed ("--session-id can only be used with --continue or
            # --resume if --fork-session is also specified"), which killed
            # every resumed build at connect() in the prototype driver.
            # Forking is not the fix either -- it would continue under a NEW
            # id, orphaning the transcript under the old one. `--resume=<id>`
            # alone already keeps the session on that same id, which is all
            # session_id bought us.
            #
            # WHICH one is not a property of the call site: both flags exit 1
            # when they disagree with the transcript on disk (`--session-id` on
            # an existing one, `--resume` on a missing one). The caller has
            # already settled it -- `ClaudeDriver._resolve_resume` probes the
            # CLI's own transcript file and puts the answer in `session`
            # ("resume"), so this factory only spells the choice out.
            session_id=None if resume else session_id,
            resume=session_id if resume else None,
            # Kept even under bypassPermissions, which the SDK warns shadows
            # this callback entirely. The warning overstates our case: probed
            # against the real CLI (see builder.py), Bash/Write do skip the
            # callback, but AskUserQuestion still reaches it -- and that is
            # the only tool we intercept (it is how a question becomes an
            # SSE `questions` event). Dropping the callback to silence the
            # warning would break that.
            can_use_tool=driver._on_can_use_tool,
            hooks={"PostToolUse": [HookMatcher(matcher="Write|Edit|MultiEdit",
                                               hooks=[driver._on_post_tool_use])]},
        )
        return ClaudeSDKClient(options=options)
    return make


def _suppress_shadowed_callback_warning() -> None:
    """Mute CanUseToolShadowedWarning -- for THIS wiring it is a false
    positive.

    See the can_use_tool comment above: the callback exists only for
    AskUserQuestion, which still reaches it under bypassPermissions. The
    warning fires on every connect() and would otherwise train operators to
    ignore backend warnings. Scoped to this one category, never a blanket
    filter, so a genuinely new SDK warning still surfaces.
    """
    import warnings
    try:
        from claude_agent_sdk import CanUseToolShadowedWarning
    except ImportError:  # older/newer SDK without the category -- nothing to mute
        return
    warnings.filterwarnings("ignore", category=CanUseToolShadowedWarning)


class ClaudeDriver:
    """Discovery agent driver: same 3-method contract as StrandsDriver.

    One connected ClaudeSDKClient, kept across turns so the subprocess and its
    transcript persist. Hook/tool callbacks run on the SDK's own tasks while
    the turn drains on the caller's loop -- both on the SAME event loop, so a
    plain list handoff is safe (no cross-thread locking, unlike
    StrandsDriver's worker-thread emit).
    """

    def __init__(self, workspace: str, rules_dir: str, config_dir: str,
                 s3: S3StoreLike, anthropic_model: str | None = None,
                 permission_mode: str = DEFAULT_PERMISSION_MODE,
                 client_factory: Callable[[dict], Any] | None = None):
        self._workspace = workspace
        self._rules_dir = rules_dir
        self._config_dir = config_dir
        self._s3 = s3
        self._anthropic_model = anthropic_model
        self._permission_mode = _validate_permission_mode(permission_mode)
        self._client_factory = client_factory or _default_client_factory(self)
        self._client: Any = None
        # A plain list, not collections.deque -- same as builder.py, for the
        # same reason: everything here runs on one event loop, and a per-turn
        # queue is never long enough for deque's O(1) popleft to matter.
        self._queue: list[AgentEvent] = []
        self._turn_active = False
        self._turn_token: object | None = None
        self._pending_question: asyncio.Future | None = None
        self._pending_payload: str | None = None
        self._pending_iid: str | None = None
        self._last_status: str | None = None
        self._current_session_id: str | None = None
        # The task draining the current turn's receive_response(). Outlives the
        # `run()` generator on purpose when a question parks the turn -- that is
        # what keeps the rest of the turn's messages for `run_answers`. See
        # _MessageReader.
        self._reader: _MessageReader | None = None

    # ---- plumbing ----

    def _emit(self, event: AgentEvent) -> None:
        """The `emit` sink handed to build_tools -- report_stage/
        submit_document push stage/document events through here."""
        self._queue.append(event)

    # There is deliberately NO `drain_queue()` here, though builder.py:183 has
    # one and this file otherwise mirrors it. A batch pop is precisely the
    # defect round 5 fixed: it moves events out of the queue that owns them and
    # into the caller's frame, which `GeneratorExit` destroys, so every one not
    # yet yielded is lost. Every consumer of `_queue` on this driver goes
    # through `_relay_queue` instead. Re-adding a batch drain would reintroduce
    # the bug at whatever call site used it.

    async def _relay_queue(self) -> AsyncIterator[AgentEvent]:
        """Yield queued tool/hook events, popping each only after delivery.

        Same ownership rule as `_pump`'s `relay` (read that one for the full
        argument): an event stays at the head of `self._queue` across the
        `yield`, so a consumer that walks away mid-sequence leaves the remainder
        owned rather than stranded in a dead generator's frame. Used by the
        error paths, which yield whatever a tool queued before the failure.
        """
        while self._queue:
            ev = self._queue[0]
            yield ev
            if self._queue and self._queue[0] is ev:
                self._queue.pop(0)

    def _resolve_resume(self, session: dict) -> dict:
        """Decide `--session-id` vs `--resume` from the transcript on disk.

        The caller's `resume` flag states INTENT (only
        `_resume_with_answers` sets it) and cannot be trusted in either
        direction, because the CLI's two failure modes are exact complements
        and both kill connect(): `--session-id` on an id that already has a
        transcript exits 1 ("already in use"), and `--resume` on an id that has
        none exits 1 ("No conversation found"). See `_transcript_exists`, where
        both are measured against the bundled binary.

        So the truth is the file, and the flag is overridden by it -- upward as
        well as downward. Upward is the case C1 was: the DEFAULT path
        (`run()` -> `_stream(resume=False)`) is what breaks after a restart,
        because the id is stable by design and the transcript outlives the
        process. Downward matters on the answers path, where a redeploy that
        also recycled the config dir would otherwise resume a session the CLI
        cannot find.

        Returns a COPY -- `session` belongs to app.py (one dict per project,
        shared by every turn), so mutating it would make one turn's resume
        decision leak into the next.
        """
        session_id, requested = _sdk_session_id(session)
        resume = _transcript_exists(self._config_dir, self._workspace,
                                    session_id)
        if resume != requested:
            # Worth a line in the log: on the answers path this is the
            # difference between continuing the conversation the question came
            # from and starting a blank one, and after a restart it is the
            # difference between a working turn and a dead project.
            _log.info("resume=%s for session %s (caller asked %s) — "
                      "transcript %s", resume, session_id, requested,
                      "found" if resume else "absent")
        out = dict(session)
        out["resume"] = resume
        return out

    async def _ensure_client(self, session: dict):
        if self._client is None:
            _suppress_shadowed_callback_warning()
            client = self._client_factory(session)
            try:
                await client.connect()
            except BaseException:
                # Do NOT cache a client that never connected. It used to be
                # assigned before this await, so one failed connect poisoned
                # the driver for the life of the process: every later turn
                # returned the same broken object and died in
                # receive_response() with "CLIConnectionError: Not connected"
                # (probed: 3 consecutive turns, `_client` non-None each time).
                # That is what turned C1 from one bad turn into a dead project,
                # and it would do the same for any transient connect failure
                # (a Bedrock throttle at startup, a config dir not yet
                # mounted). Leaving the cache empty makes the next turn build a
                # fresh client and re-probe the transcript.
                #
                # BaseException, not Exception: a cancelled connect (SSE
                # disconnect mid-turn) leaves an equally unusable client.
                self._client = None
                raise
            self._client = client
        return self._client

    async def _on_post_tool_use(self, input_data, tool_use_id, context) -> dict:
        name = input_data.get("tool_name", "")
        if name in _FILE_TOOLS:
            fp = (input_data.get("tool_input") or {}).get("file_path", "")
            rel = _rel(fp, self._workspace)
            if rel is None:
                self._queue.append(AgentEvent(
                    kind="status", text="file outside workspace ignored"))
            else:
                self._queue.append(AgentEvent(kind="file_changed", path=rel))
        return {}

    def _answer_to_sdk(self, value: str, sdk_options: list[dict]) -> str:
        """QuestionForm answer value → SDK label(s). Accepted forms:
        "A" | "A,C" | "A: note" | free text (unmatched passes through)."""
        def label(letter: str) -> str | None:
            idx = _LETTERS.find(letter.strip())
            if 0 <= idx < len(sdk_options):
                return sdk_options[idx].get("label", "")
            return None
        if ":" in value:
            head, _, note = value.partition(":")
            l = label(head)
            if l is not None:
                return f"{l}:{note}"
        parts = [label(p) for p in value.split(",")]
        if parts and all(p is not None for p in parts):
            return ", ".join(parts)
        return value  # free text (Other)

    # ---- the question round trip ----

    async def _on_can_use_tool(self, tool_name, input_data, context):
        """AskUserQuestion → `questions` event, then park until answered.

        The SDK dispatches this on its own task while the CLI blocks waiting
        for the permission response, so `receive_response()` yields nothing
        for as long as we stay suspended here -- which is exactly what
        `_pump`'s queue poll exists to cope with.
        """
        from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny
        if tool_name != "AskUserQuestion":
            return PermissionResultAllow(updated_input=input_data)
        import uuid
        sdk_questions = input_data.get("questions", [])
        # question_file_from_sdk raises ValueError on unusable input (e.g. a
        # question with zero options) -- deny with a message the model can
        # read and retry from, instead of letting the exception escape.
        # PermissionResultDeny is the SDK-native way to hand the model an
        # explanation; the can_use_tool contract only speaks PermissionResult,
        # so no other shape is invented here.
        try:
            qfile = question_file_from_sdk(sdk_questions, name="discovery-questions")
        except ValueError as e:
            _log.warning("AskUserQuestion payload rejected: %s", e)
            return PermissionResultDeny(
                message=f"질문을 만들 수 없다: {e}\n"
                        "각 질문에 옵션을 최소 1개 넣어 AskUserQuestion을 다시 호출해라.")
        iid = uuid.uuid4().hex
        payload = json.dumps({"interrupt_id": iid, "questions": qfile},
                             ensure_ascii=False)
        self._pending_payload = payload
        self._pending_iid = iid
        # sdk_questions is NOT stashed on self: this coroutine keeps it in its
        # own frame across the await, and the restart path reads it back from
        # S3 instead. A second copy on the instance would be one more thing to
        # keep in sync with _clear_pending_state.
        loop = asyncio.get_running_loop()
        self._pending_question = loop.create_future()
        await self._save_pending_quietly(iid, qfile, sdk_questions)
        self._queue.append(AgentEvent(kind="questions", payload=payload))
        try:
            answers = await self._pending_question  # stays open until answered
        except asyncio.CancelledError:
            # Nothing in Discovery cancels this deliberately (there is no
            # interrupt route), so a cancellation here means the turn is being
            # torn down around us -- leaving _pending_payload set would make
            # pending() advertise a question no future is listening on.
            self._clear_pending_state()
            raise
        try:
            # "number -> letter/text" (our contract) → "question text ->
            # label" (SDK contract).
            sdk_answers = {}
            for k, v in answers.items():
                try:
                    q = sdk_questions[int(k) - 1]
                except (ValueError, IndexError):
                    continue
                sdk_answers[q.get("question", "")] = self._answer_to_sdk(
                    v, q.get("options", []))
        finally:
            self._clear_pending_state()
        return PermissionResultAllow(updated_input={
            "questions": sdk_questions,
            "answers": sdk_answers,
        })

    def _clear_pending_state(self) -> None:
        self._pending_payload = None
        self._pending_question = None
        self._pending_iid = None

    async def _save_pending_quietly(self, iid: str, qfile: dict,
                                    sdk_questions: list) -> None:
        """Mirror the pending question to S3. Never fails the turn.

        Persistence is restore convenience -- losing an in-flight question
        over an S3 hiccup is the bigger loss (the same judgment
        runner._sync_abandoned_turn makes about its own best-effort sync).
        """
        session_id = self._current_session_id or ""
        if not session_id:
            # load_pending validates session_id as a non-empty string, so a
            # record written without one is unreadable -- writing it anyway
            # would only leave junk that masks nothing. In-memory pending()
            # still covers the same-process refresh.
            _log.warning("no session id for pending-question persist — skipped")
            return
        try:
            await save_pending(self._s3, interrupt_id=iid, questions=qfile,
                               sdk_questions=sdk_questions, session_id=session_id)
        except Exception:
            _log.exception("pending-question S3 persist failed")

    async def _clear_pending_quietly(self) -> None:
        try:
            await clear_pending(self._s3)
        except Exception:
            _log.exception("pending-question S3 clear failed")

    # ---- message translation + the turn pump ----

    def _translate(self, msg) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        tname = type(msg).__name__
        if tname == "AssistantMessage":
            for block in getattr(msg, "content", []):
                btype = type(block).__name__
                if btype == "TextBlock":
                    events.append(AgentEvent(kind="message", text=block.text))
                elif btype == "ToolUseBlock":
                    if block.name != self._last_status:
                        self._last_status = block.name
                        events.append(AgentEvent(kind="status", text=block.name))
        elif tname == "ResultMessage":
            events.append(AgentEvent(kind="done"))
        return events

    def _retire_reader(self) -> None:
        """Drop the reader owning the PREVIOUS turn's iterator.

        Called only where the old turn is being replaced (a new `query()`) or
        the subprocess is going away (`disconnect`) -- never while the turn it
        belongs to may still continue, because cancelling the reader is the one
        remaining way to lose a message that was handed to it. When a question
        is parked the reader is deliberately left running: it is what collects
        the rest of the turn for `run_answers` to relay.
        """
        reader, self._reader = self._reader, None
        if reader is not None and not reader.task.done():
            reader.task.cancel()

    async def _pump(self, reader: "_MessageReader") -> AsyncIterator[AgentEvent]:
        """Relay one turn: the reader's messages, raced against the callback queue.

        Two sources feed a turn, and only one of them can be awaited. The
        hook/tool-callback queue (`self._queue`) is filled synchronously by
        callbacks running on the SDK's own tasks and has nothing to await, so it
        is POLLED on `_POLL_SECONDS`. That poll is not an optimization: while an
        AskUserQuestion is parked the CLI is blocked on the permission response
        and the message stream yields nothing at all, so a driver that only
        awaited messages would never let the queued `questions` event reach the
        SSE stream (this is builder.py's hard-won detail, carried across).

        Messages come from `reader.inbox`, never from an awaited receive. That
        is THE structural property of this function, and the reason it no longer
        needs to reason about interleavings: `_pump` owns no cancellable
        receive, so no suspension of `_pump` -- not a `yield`, not an `await`,
        not the `GeneratorExit` of an abandoned generator -- can destroy a
        message. See `_MessageReader` for the full argument, including why the
        peek-future shape it replaces had no safe suspension protocol and cost
        three review rounds one lost-message defect each.

        Where this DIVERGES from builder.py: a queued `questions` event ends the
        turn (`questions`, then `done`, then return), because Discovery answers
        arrive on a separate stream -- see the module docstring. The suspended
        can_use_tool callback is left parked on its future, and the READER IS
        LEFT RUNNING, for `run_answers` to relay through `_continue_after_answers`.

        Two invariants this function owns, both verified by tests:

        1. Exactly one terminal event, always LAST. `_translate`'s `done` (from
           a ResultMessage) is never yielded; it only sets `ended`, so the
           terminal event has a single origin and cannot be doubled.
           `frontend/lib/api/sse.ts:29` closes the EventSource on `done`, so
           anything after it never reaches `onEvent` -- a `stage`/`document`
           emitted by a tool during the same ready burst would be silently
           dropped and the sidebar/document panel would go stale. Hence the
           terminal harvest below drains BOTH sources to exhaustion before
           `done`.
        2. No event this driver has produced is dropped. Both invariants rest on
           ONE ownership rule, and it is checkable by reading rather than by
           reasoning about interleavings:

               An event is in exactly one place that outlives this generator,
               and it leaves that place only AFTER the consumer has received it.

           The places are `reader.outbox` (translated messages) and
           `self._queue` (tool/hook events). `_relay` below is the only code
           that yields, and it is the only code that removes an item -- popping
           after the `yield` returns, never before. So an item is either
           already delivered or still owned; `GeneratorExit` at any yield leaves
           the remainder owned, and the next pump over the same reader relays
           it.

           This is the rule the previous round got wrong. It moved message
           ownership out of anyio's buffer and into the driver (which is what
           fixed the abandoned-receive class), but then batch-popped the whole
           inbox into a LOCAL LIST before yielding any of it -- so the
           not-yet-yielded remainder lived in this generator's frame, which
           `GeneratorExit` destroys. Reproduced through the real AgentRunner: a
           3-message burst, consumer abandons after the first, 2 lost. The batch
           pop is exactly what made this invariant false, so no batch pop.
        """
        asked = False
        ended = False

        def translate_into_outbox() -> None:
            """Move messages inbox -> outbox, translating. Never yields.

            Wholly synchronous, so a message is never in neither place: it
            leaves `inbox` and enters `outbox` with no suspension in between.
            `outbox` lives on the reader, so anything still there when this
            generator dies belongs to the next pump over the same reader.
            """
            nonlocal ended
            while reader.inbox and not ended:
                for ev in self._translate(reader.inbox.pop(0)):
                    if ev.kind == "done":
                        ended = True   # terminal event comes from the exit below
                        continue
                    reader.outbox.append(ev)

        async def relay():
            """Yield from both owned queues, popping only after delivery.

            `queue[0]` then `pop(0)` -- not `pop(0)` then `yield` -- is the whole
            fix. If the consumer abandons us at the `yield`, the event is still
            at the head of its owned queue.
            """
            nonlocal asked
            for queue in (reader.outbox, self._queue):
                while queue:
                    ev = queue[0]
                    yield ev
                    # Reached only if the consumer came back for the next item,
                    # i.e. it really received this one.
                    if queue and queue[0] is ev:
                        queue.pop(0)
                    asked = asked or ev.kind == "questions"

        while True:
            # The only cancellable wait in this function, and it waits on an
            # Event -- which carries no payload, so a cancellation here cannot
            # lose anything.
            await reader.settle(_POLL_SECONDS)
            translate_into_outbox()
            async for ev in relay():
                yield ev
            if reader.ended and not reader.inbox:
                ended = True
            if asked or ended:
                break
        if reader.error is not None:
            # Surfaced on the caller's stack so `_stream` can degrade it to the
            # contract's "agent turn failed" -- messages already relayed above
            # have reached the consumer first.
            raise reader.error
        # Terminal relay. Every `yield` above handed control to the scheduler,
        # which is exactly when the reader can append another message and a tool
        # callback can queue another event -- so drain both to exhaustion before
        # the terminal event, or an event that arrived during those yields would
        # land after `done` (invariant 1) or wait for the next turn (invariant 2,
        # correct but needlessly late). Terminates because both sources are now
        # finished: after `asked` the CLI is blocked on the permission response,
        # and after `ended` the iterator is done.
        while True:
            translate_into_outbox()
            if not (reader.outbox or self._queue):
                break
            async for ev in relay():
                yield ev
        yield AgentEvent(kind="done")

    # ---- the single-turn slot ----
    #
    # Ownership lives in `run`/`run_answers` -- the two contract entry points,
    # which are always the OUTERMOST generator -- and nowhere else. That is not
    # stylistic: `aclose()` runs only the outermost generator's `finally`
    # synchronously, and a `finally` on a nested generator (`_stream` inside
    # `run`) needs GeneratorExit to propagate down, which takes extra event-loop
    # ticks. Measured: with the flag released in `_stream`, `_turn_active` was
    # still True immediately after `await agen.aclose()` and only cleared after
    # two bare `await asyncio.sleep(0)`.
    #
    # Why that mattered: runner.py:144-152 routinely abandons this generator
    # (SSE disconnect, proxy timeout, user navigating away) and clears its own
    # `_turn_active` synchronously in the same `finally`. So a browser that
    # reconnects on the very next tick passed runner's guard and was then
    # rejected by ours with "turn already in progress" -- the user's retry
    # bounced off a turn that no longer existed.

    def _acquire_turn(self) -> object | None:
        """Claim the turn slot; None if one is already running. The token
        identifies THIS turn so a rejected caller cannot release the slot the
        live turn is holding."""
        if self._turn_active:
            return None
        self._turn_active = True
        self._turn_token = object()
        return self._turn_token

    def _release_turn(self, token: object) -> None:
        if self._turn_token is token:
            self._turn_active = False
            self._turn_token = None

    async def _stream(self, text: str, session: dict,
                      resume: bool = False) -> AsyncIterator[AgentEvent]:
        """Assumes the caller already holds the turn slot (see above)."""
        self._last_status = None
        self._current_session_id = session.get("session_id")
        try:
            # `resume` is only what the CALLER wants; `_resolve_resume` decides
            # from the CLI's transcript, because both flags are fatal when they
            # disagree with the disk. Building the dict here (rather than
            # inside _ensure_client) keeps the client factory's contract
            # unchanged: it still receives a session dict with `resume`
            # already settled.
            connect_session = self._resolve_resume(dict(session,
                                                       resume=resume))
            client = await self._ensure_client(connect_session)
            # A new `query()` starts a new turn, so anything the previous turn's
            # reader was still holding belongs to a turn nobody will relay --
            # this is the only place it is safe to drop it.
            self._retire_reader()
            await client.query(text)
            self._reader = _MessageReader(client.receive_response().__aiter__())
            async for ev in self._pump(self._reader):
                yield ev
        except Exception:
            _log.exception("claude sdk turn failed")
            async for ev in self._relay_queue():
                yield ev
            yield AgentEvent(kind="error", text="agent turn failed")
            return

    async def _continue_after_answers(self) -> AsyncIterator[AgentEvent]:
        """Relay the REST of a turn that `_pump` parked on a question.

        No `query()` here -- the turn is still mid-flight; the CLI was only
        waiting for the permission response that resolving the future produces.

        And no new iterator either: the SAME `_MessageReader` from the question
        turn is still reading, which is what makes this safe. It never stopped,
        so messages the CLI sent while the question was on screen -- or while
        the SSE consumer was away, or after it abandoned the `run()` generator
        entirely (runner.py:144-152) -- are sitting in `reader.inbox` waiting to
        be relayed. This is precisely what the old shape got wrong: it opened a
        FRESH iterator and depended on anyio having buffered those messages,
        which it had not, because `send_nowait` hands an item straight to a
        parked receiver (see _MessageReader).

        Assumes the caller already holds the turn slot.
        """
        if self._client is None:  # defensive: no client, nothing to resume
            yield AgentEvent(kind="error", text="agent turn failed")
            return
        reader = self._reader
        if reader is None:
            # No reader means no turn in flight for this future to belong to.
            # Reported as a turn failure rather than a silent empty turn, since
            # the answers the user submitted have nowhere to go.
            _log.warning("answers resolved with no reader for the turn")
            yield AgentEvent(kind="error", text="agent turn failed")
            return
        try:
            async for ev in self._pump(reader):
                yield ev
        except Exception:
            _log.exception("claude sdk answer turn failed")
            async for ev in self._relay_queue():
                yield ev
            yield AgentEvent(kind="error", text="agent turn failed")
            return

    async def _resume_with_answers(self, interrupt_id: str,
                                   answers: dict[str, str],
                                   session: dict) -> AsyncIterator[AgentEvent]:
        """The backend restarted: no future to resolve, so resume the session
        and deliver the answers as an ordinary text turn. The model already
        has the question's context in the transcript, so all this prompt has
        to do is say which option was chosen.

        `resume=True` below is a REQUEST, not a guarantee: `_resolve_resume`
        overrules it when the transcript is gone (a replaced instance, a
        recycled config dir), because `--resume` on a missing session exits 1
        and would turn the user's answer into "agent turn failed". The answers
        then land in a fresh session -- the model loses the question's context,
        which is why the prompt restates the question and the chosen label
        rather than relying on the transcript.

        Assumes the caller already holds the turn slot.
        """
        data = await load_pending(self._s3)
        if data is None:
            yield AgentEvent(kind="error", text="no pending questions")
            return
        # Same guard as the live path, for the same reason -- an old browser
        # tab answering a superseded round. Without it the stored questions
        # were translated against the CALLER's answers regardless of which
        # round they belonged to, so a stale tab silently answered the current
        # question with the wrong round's answers AND deleted the real pending
        # record on the way out (reproduced: seeded "i-CURRENT"/"NEW question",
        # submitted "i-STALE" -> prompt said `- NEW question → 진행`, record
        # gone). Refuse and leave the record intact so the live form still works.
        if data.get("interrupt_id") != interrupt_id:
            _log.warning("answers for a superseded question round — refused")
            yield AgentEvent(kind="error", text="no pending questions")
            return
        sdk_questions = data.get("sdk_questions") or []
        lines = []
        for k, v in answers.items():
            try:
                q = sdk_questions[int(k) - 1]
            except (ValueError, IndexError, KeyError, TypeError):
                continue
            label = self._answer_to_sdk(v, q.get("options", []))
            lines.append(f"- {q.get('question', '')} → {label}")
        # The last line is a machine-readable record of the round these
        # answers belong to. It is in the prompt (not just the log) because
        # after a restart the S3 pending record is deleted on the way out and
        # the transcript becomes the ONLY durable trace of which interrupt a
        # given set of answers resolved -- which is what you need when a
        # resumed session answers the wrong question. It is also what the
        # contract test's echo fake reads back to prove the caller's
        # interrupt_id/answers reached the SDK call.
        prompt = ("[질문 답변] 앞서 드린 질문에 사용자가 답했습니다. "
                  "이 답변을 반영해 이어서 진행해 주세요.\n"
                  + "\n".join(lines)
                  + "\n(답변 기록)\n"
                  + json.dumps({"interrupt_id": interrupt_id,
                                "answers": answers}, ensure_ascii=False))
        await self._clear_pending_quietly()
        self._clear_pending_state()
        async for ev in self._stream(prompt, session, resume=True):
            yield ev

    # ---- driver contract (same three methods as StrandsDriver) ----

    def _place_rules(self) -> bool:
        """Rule placement happens every turn -- the workspace is volatile
        (runner reconstructs it from S3 each turn, and runner.py:36 restores
        only aiplc-docs/, prototype/, uploads/ -- never the rules) and without
        them the agent runs with no workflow to follow, which shows up as an
        empty conversation rather than an error. False means the turn must be
        abandoned."""
        try:
            place_rules(self._workspace, self._rules_dir)
            return True
        except Exception:
            _log.exception("rule placement failed")
            return False

    async def run(self, text: str, session: dict) -> AsyncIterator[AgentEvent]:
        """Contract: runner.py:129 calls this."""
        if not self._place_rules():
            yield AgentEvent(kind="error", text="agent turn failed")
            return
        # The concurrency guard comes BEFORE the pending-question short-circuit
        # below: if a turn is genuinely still streaming, "turn already in
        # progress" is the accurate report, and re-surfacing the question
        # instead would tell the caller to answer a form while the turn it
        # belongs to is still being consumed by someone else. A question parked
        # by a turn that already RETURNED leaves the slot free (run() releases
        # it on the way out), so that case still reaches the short-circuit.
        token = self._acquire_turn()
        if token is None:
            yield AgentEvent(kind="error", text="turn already in progress")
            return
        try:
            # Mirrors StrandsDriver's B1 short-circuit (driver.py:166-177) for
            # the same class of failure, with a Claude-SDK-specific cause:
            # while a question is parked, the CLI is blocked waiting for the
            # permission response, so `query()` would be accepted and then
            # never answered -- the turn would poll to nowhere until the client
            # gave up. Re-surface the pending question instead of calling the
            # model.
            fut = self._pending_question
            if fut is not None and not fut.done():
                yield AgentEvent(kind="message", text=_ANSWER_FIRST)
                if self._pending_payload is not None:
                    yield AgentEvent(kind="questions",
                                     payload=self._pending_payload)
                yield AgentEvent(kind="done")
                return
            async for ev in self._stream(text, session):
                yield ev
        finally:
            self._release_turn(token)

    async def run_answers(self, interrupt_id: str, answers: dict[str, str],
                          session: dict) -> AsyncIterator[AgentEvent]:
        """Contract: runner.py:167 calls this.

        Two paths, and BOTH validate `interrupt_id` against the round they are
        about to answer -- a mismatch is an old tab answering a superseded
        question, and is refused with the contract's "no pending questions".

        A waiting future means a normal round trip: resolve it, and the answers
        reach the model as the AskUserQuestion tool result. No future means the
        backend restarted, so resume the session and deliver the answers as a
        text turn instead.
        """
        # The rules go down on this path too, and this is the ONE path where
        # the workspace is guaranteed cold: no-future means a redeploy, so the
        # agent's first post-restart action would otherwise run with no
        # CLAUDE.md at all. Cheap and idempotent, so it is unconditional
        # rather than branch-dependent.
        if not self._place_rules():
            yield AgentEvent(kind="error", text="agent turn failed")
            return
        token = self._acquire_turn()
        if token is None:
            yield AgentEvent(kind="error", text="turn already in progress")
            return
        try:
            fut = self._pending_question
            if fut is not None and not fut.done():
                if self._pending_iid != interrupt_id:
                    # Resolving the live future with a superseded round's
                    # answers would feed the model answers to a different
                    # question; falling through to the resume path is no
                    # better, since `query()` while the CLI is blocked hangs.
                    yield AgentEvent(kind="error", text="no pending questions")
                    return
                fut.set_result(answers)
                await self._clear_pending_quietly()
                # Drop any `questions` event for THIS round that is still owned
                # and undelivered. It exists when the question turn was abandoned
                # before the card reached the browser (the user then got it from
                # GET /pending instead), and relaying it now would re-show a card
                # the user has just answered -- and answering it a second time is
                # refused with "no pending questions", since the future is gone.
                # The answers this call carries are that event's whole purpose,
                # so it has been fulfilled, not lost.
                self._queue[:] = [ev for ev in self._queue
                                  if not (ev.kind == "questions"
                                          and _iid_of(ev) == interrupt_id)]
                async for ev in self._continue_after_answers():
                    yield ev
                return
            async for ev in self._resume_with_answers(interrupt_id, answers,
                                                      session):
                yield ev
        finally:
            self._release_turn(token)

    async def pending(self, session: dict) -> str | None:
        """Contract: runner.py:183 calls this. In-memory first, then S3 --
        covers both a same-process refresh and a backend restart."""
        if self._pending_payload is not None:
            return self._pending_payload
        data = await load_pending(self._s3)
        if data is None:
            return None
        return json.dumps({"interrupt_id": data["interrupt_id"],
                           "questions": data["questions"]},
                          ensure_ascii=False)

    # ---- lifecycle (beyond the three-method contract) ----

    async def disconnect(self) -> None:
        """Tear down the claude subprocess. Idempotent.

        NOT part of the driver contract and NOT yet called by anything --
        runner.stop() only rmtree's the local workspace, so today every deleted
        project leaks a ~300-500MB `claude` process for the life of the
        backend. builder.py:395-404 has the same method and proto/session.py
        calls it on close/idle-timeout; providing it here lets Task 8 wire
        runner.stop() to it without touching this file (runner.py is off-limits
        in this task).

        A pending question cannot survive the teardown -- its future is
        abandoned along with the subprocess, so leaving _pending_payload set
        would make pending() advertise a question that can never be answered
        (builder.interrupt() clears the same state for the same reason).

        Two more places the same dead question hides, both carry-forward from
        Task 6 and both real once this method is actually wired (Task 8):

        - The S3 mirror (`save_pending`). `pending()` checks the in-memory
          `_pending_payload` FIRST but falls back to `load_pending(self._s3)`
          when that is None -- so clearing only the in-memory copy just moves
          the dead question onto the other path; it does not remove it.
        - `self._queue` may still hold an unpopped `questions` AgentEvent for
          this exact question, if the turn that raised it was abandoned
          before the event was delivered (`_pump`'s ownership rule leaves it
          at the head of the queue for the next turn to relay -- see `_pump`'s
          docstring). With the subprocess gone, "the next turn" is a brand new
          one that has nothing to do with that question, so relaying it would
          hand the user a card for a question no future will ever resolve.
        """
        if self._pending_question is not None and not self._pending_question.done():
            self._pending_question.cancel()
        self._clear_pending_state()
        await self._clear_pending_quietly()
        self._queue[:] = [ev for ev in self._queue if ev.kind != "questions"]
        # The subprocess is going away, so there is no turn left for the reader
        # to collect and nothing that could relay what it holds.
        self._retire_reader()
        client, self._client = self._client, None
        if client is None:
            return
        try:
            await client.disconnect()
        except Exception:
            _log.exception("claude driver disconnect failed")
