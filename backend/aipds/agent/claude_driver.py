# backend/aipds/agent/claude_driver.py — Discovery agent driver on the
# Claude Agent SDK, running IN-PROCESS (no VM). The ONLY Discovery driver; the
# `strands` fallback was deleted (see app.driver_factory for why). It still
# implements runner.py's three-method contract, asserted by
# tests/driver_contract.py's assert_driver_contract -- kept as a separate
# contract file so the interface runner.py depends on stays written down.
#
# Most of the SDK plumbing below (client construction, can_use_tool
# interception, PostToolUse hook, event translation, the queue-polling race
# while a question is pending, --session-id/--resume conflict avoidance) is
# COPIED from aipds/proto/builder.py -- the prototype build driver that
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
import time
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from aipds.agent import prompts
from aipds.agent.answer_store import save_answers
from aipds.agent.discovery_guard import (WRITE_TOOLS, bash_denial,
                                              write_denial)
from aipds.agent.pending_store import (clear_pending, load_pending,
                                              load_pending_file, save_pending,
                                              save_pending_file)
from aipds.agent import reconcile
from aipds.agent.question_file_answers import (looks_like_question_file,
                                                    record_answers)
from aipds.agent.questions_payload import (normalize_sdk_questions,
                                                 question_file_from_sdk)
from aipds.agent.session_store import DiscoverySessionStore
from aipds.agent.workspace_rules import place_rules
from aipds.cli_settings import cli_context_env
from aipds.models import AgentEvent
from aipds.pathsafe import workspace_relative as _rel
from aipds.performance import log_performance
from aipds.s3store import S3StoreLike
from aipds.tool_trace import tool_detail
from aipds.workspace_sync import publish_file

_log = logging.getLogger("aipds.agent")

#: The file-writing tools. Owned by discovery_guard -- PostToolUse (observing)
#: and PreToolUse (blocking) must see **the same** set. Two copies means a tool
#: gets added to only one of them, opening an "observed but not blocked" hole.
_FILE_TOOLS = WRITE_TOOLS
_LETTERS = "ABCDEFGHIJ"

# How long `_pump` blocks on the next SDK message before checking the
# callback/hook queue again. Copied from builder.py:319 -- see `_pump`'s
# docstring for why the poll exists at all.
_POLL_SECONDS = 0.05

# The turn-failure text the CLI's `ResultMessage.is_error` produces, and the
# "answer the open question first" line, both live in agent/prompts.py now --
# they are per-project-language, so they cannot be module constants. See that
# module's header for why (2026-08-04: an English project was reading Korean
# on every one of these paths).
#
# They are actionable prose, unlike the internal "agent turn failed" the other
# error paths use: those are exception paths where the frontend substitutes its
# own copy, whereas these are ordinary ends of a turn the user watched happen.

# The `ResultMessage.terminal_reason` values that mean "cancelled"
# (claude_agent_sdk/types.py:1249-1257): a turn cut by interrupt() arrives as one
# of these two, whether it was mid-stream or mid-tool. They are collected here
# rather than inlined in the branch so that a third value appearing in the SDK
# needs one edit in one place.
_INTERRUPTED_TERMINAL_REASONS = frozenset({"aborted_streaming", "aborted_tools"})

#: The `status` event text that marks an interrupted turn. **A machine signal,
#: not human-facing wording** -- frontend/lib/useWorkspaceStream.ts compares
#: against this value to set its interrupted flag, and the on-screen wording
#: ("중단됨"/"Interrupted" -- quoted verbatim so a reader can grep the i18n keys)
#: is drawn by the frontend in the UI language.
#: proto/builder.py uses the same value: if the two drivers diverge, the frontend
#: behaves differently depending on which path produced the turn.
#:
#: Note that the reason this one is language-neutral differs from the approval
#: marker (frontend/lib/approvalMarker.ts): that marker goes to the agent and stays
#: in the transcript as the user's bubble, so it has to be a word in the project's
#: language. This marker only ever exists in the live SSE queue and no human reads
#: it.
INTERRUPTED_MARKER = "interrupted"

# Discovery runs with a human in the loop watching the chat, unlike the
# unattended prototype build -- but AskUserQuestion is still routed through
# can_use_tool (see _on_can_use_tool below) and every other tool must execute
# without a separate approval round-trip, since the UI's only approval
# mechanism IS the questions card. Kept as the same default as builder.py's
# DEFAULT_PERMISSION_MODE for the same reason: any mode that can prompt stalls
# the turn with no operator to answer the CLI-level prompt.
DEFAULT_PERMISSION_MODE = "bypassPermissions"

#: Whether the PostToolUse hook asks question files **verbatim from the file**.
#: **On by default.**
#:
#: This is the only question path: when it is on, AskUserQuestion calls are denied
#: and the denial sends the model back to writing a question file. Having both on
#: puts the same question on screen twice (the agent writes the file, the hook
#: shows the card, and then the tool call follows).
#:
#: **Why on by default.** Rebuilding questions already written to a file as this
#: tool's input mangled 15 of 19 measured questions (79%) -- 11 cases of
#: substituted Hangul characters, and 4 answers lost to abbreviation. Reading the
#: file verbatim removes that entire class of failure.
#:
#: The garbled text is kept verbatim because it IS the evidence: the screen showed
#: "푸로토하이프가 … 어느 쉘입니까?" where the original asked which shell the
#: prototype should use -- the characters themselves were substituted, so
#: paraphrasing it in English would destroy the very thing being reported.
#:
#: Flipped on 2026-08-17 after a full pass through a real Discovery turn: the hook
#: showed the card, the turn stopped, the answers were recorded into the file, and
#: **on the next turn the model read those answers and carried the workflow on**
#: (it did not call AskUserQuestion again). That last point was the only unverified
#: item.
#:
#: Escape hatch: set this env to a falsy value to return to the old path. On the
#: instance, user-data injects the value through systemd `Environment=`, so editing
#: that file requires an instance replacement -- creating a gitignored
#: `backend/.env` instead lets you turn it off without a redeployment, because
#: `aipds-update` will not revert an untracked file.
FILE_QUESTIONS_ENV = "AIPDS_FILE_QUESTIONS"

#: The values that read a boolean env as **off**. The mirror image of
#: `_TRUTHY` in cli_settings.py and routes/proto_public.py: a setting that
#: defaults to ON has to read "no value" as on, so it needs a list of what turns
#: it off rather than a list of what turns it on.
_FALSY = {"0", "false", "no", "off"}


def _file_questions_enabled() -> bool:
    import os
    return os.environ.get(FILE_QUESTIONS_ENV, "").strip().lower() not in _FALSY



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
        # Set when the CLI's ResultMessage says the turn FAILED (is_error).
        # Not an exception: the turn ran, produced messages, and ended
        # cleanly -- it just ended in failure. Kept separate from `error`
        # (which is a raised exception from the iterator) because the two need
        # different terminal events: `error` propagates out of `_pump` for
        # `_stream` to degrade, while this one only changes which terminal
        # event `_pump` emits. See `_translate`'s ResultMessage branch.
        self.failed = False
        self.transcript_flushed = False
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
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"aipds:{raw}")), resume


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
    a deploy wipes /opt/aipds -- and the drift is silent in both
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
            ClaudeAgentOptions, ClaudeSDKClient,
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
        # When auto-compaction kicks in. Unset means the key is absent and the
        # CLI default applies. This is the switch that delays Discovery writing
        # its late-stage documents from a summarised context (cli_settings'
        # header records the measurement: 264k -> 53k).
        env.update(cli_context_env())
        session_id, resume = _sdk_session_id(session)
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
            # **There are no custom tools.** `mcp_servers` and `allowed_tools`
            # are not passed at all (their SDK defaults are `{}`/`[]`, so the
            # built-in tools are unaffected).
            #
            # There were three: `report_stage` and `handoff_prototype` moved to the
            # PostToolUse hook on 2026-08-18, `submit_document` on 2026-08-21. The
            # test was the same every time -- **can the signal be derived from the
            # workspace?** A tool is silent unless the model calls it, and that
            # silence was measured three times (agent/reconcile.py's header plus
            # useWorkspaceStream.ts:177).
            #
            # Removing a tool wins back two things: the inference round trip each
            # call added, and the tool description carried in the context every
            # turn. Bringing one back spends those again, so it has to first
            # demonstrate that its signal cannot be derived from a file.
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
            # Mirror transcript batches to S3. Without it the conversation
            # lives ONLY in the CLI's local .jsonl, so an EC2 replacement or
            # redeploy loses the whole Discovery history -- and chat restore had
            # nothing durable to read (session_history was still pointed at
            # strands' S3 layout, which this driver never writes). Same
            # mechanism the prototype builder already uses (proto/builder.py's
            # session_store), different key prefix.
            session_store=driver._session_store,
            # Flush once at AI-PDS's explicit turn boundary. `_pump`
            # handles normal/question terminals, while stream finally blocks
            # cover errors and abandoned SSE consumers.
            session_store_flush="batched",
            # Kept even under bypassPermissions, which the SDK warns shadows
            # this callback entirely. The warning overstates our case: probed
            # against the real CLI (see builder.py), Bash/Write do skip the
            # callback, but AskUserQuestion still reaches it -- and that is
            # the only tool we intercept (it is how a question becomes an
            # SSE `questions` event). Dropping the callback to silence the
            # warning would break that.
            can_use_tool=driver._on_can_use_tool,
            # PreToolUse is this product's only gate that actually takes effect.
            # The can_use_tool above is not invoked for Write/Bash under
            # bypassPermissions -- the SDK states both the fact and the remedy
            # itself (types.py's _get_can_use_tool_shadowed_warning: "To gate every
            # tool call, use a PreToolUse hook instead").
            #
            # **AskUserQuestion is deliberately absent from the matcher.** Per
            # types.py's can_use_tool description, a PreToolUse hook returning
            # *allow* also skips can_use_tool -- and the question interception lives
            # in that callback, so doing so would kill the whole question round
            # trip. For the same reason _on_pre_tool_use returns an **empty dict**
            # rather than "allow" when it lets a call through.
            hooks={
                "PreToolUse": [HookMatcher(matcher="Write|Edit|MultiEdit|Bash",
                                           hooks=[driver._on_pre_tool_use])],
                "PostToolUse": [HookMatcher(matcher="Write|Edit|MultiEdit",
                                            hooks=[driver._on_post_tool_use])],
            },
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
    """Discovery agent driver. Implements runner.py's 3-method contract.

    One connected ClaudeSDKClient, kept across turns so the subprocess and its
    transcript persist. Hook/tool callbacks run on the SDK's own tasks while
    the turn drains on the caller's loop -- both on the SAME event loop, so a
    plain list handoff is safe (no cross-thread locking).
    """

    def __init__(self, workspace: str, rules_dir: str, config_dir: str,
                 s3: S3StoreLike, anthropic_model: str | None = None,
                 language: str = "ko",
                 permission_mode: str = DEFAULT_PERMISSION_MODE,
                 client_factory: Callable[[dict], Any] | None = None,
                 session_store: Any = None):
        self._workspace = workspace
        self._rules_dir = rules_dir
        self._config_dir = config_dir
        self._s3 = s3
        # Transcript mirror. Built here rather than per-turn so the sequence
        # counter it seeds from S3 is reused across turns of one process
        # (session_store.py's header explains why that seeding exists).
        # Injectable so a test can pass None and skip S3 entirely.
        self._session_store: Any = (session_store if session_store is not None
                                    else DiscoverySessionStore(s3))
        self._anthropic_model = anthropic_model
        # This project's output language. It flows to two places: place_rules
        # (the language directive in the workspace CLAUDE.md) and the
        # model/user-facing texts this driver builds (agent/prompts.py). Back when
        # there were three, the third was the custom tools' descriptions and return
        # strings; that channel disappeared when Discovery's tools did on
        # 2026-08-21.
        #
        # At first place_rules was the only one, and that was the defect: switching
        # the directive to English still left tool descriptions and refusals in
        # Korean, entering the model's context every turn. The shared
        # CLAUDE_CONFIG_DIR is shared by every project and so still cannot carry a
        # project language -- which is why the documents there have to be
        # language-neutral.
        self._language = language
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
        # (tool name, detail) -- collapsing on the name alone would squash
        # consecutive Reads of *different* files into one line. The
        # INTERRUPTED_MARKER comparison is done on event.text, so making this key a
        # tuple does not affect that path.
        self._last_status: tuple[str, str | None] | None = None
        # rel path -> the set of **unanswered questions already asked** from that
        # file. The guard against asking the same set twice (see
        # _file_question_round). It survives across turns because the driver
        # instance lives for the life of the project. A backend restart empties it,
        # but by then the answers are already in the file, so the "no unanswered
        # questions" condition prevents a re-ask.
        self._asked_question_sets: dict[str, tuple[str, ...]] = {}
        # rel path -> the content whose parse failure has already been reported.
        # Avoids repeating the same note for the same content, while reporting
        # again when the content changes (see _on_post_tool_use).
        self._unparsed_noted: dict[str, str] = {}
        # Stage name -> the status already emitted for it. The diff cursor for
        # the `stage` events derived from `aiplc-state.md`
        # (agent/reconcile.stage_events). The frontend accumulates these events, so
        # re-emitting the same status grows the sidebar list.
        #
        # This does not depend on the driver instance living for the life of the
        # project: if a backend restart empties the cursor, the next state file
        # write re-emits the whole set once -- and by then the frontend has already
        # built the screen from the REST read.
        self._stage_status: dict[str, str] = {}
        # The prototype ids already announced via `prototype_ready`. Announcing
        # twice puts two cards in the chat (agent/reconcile.prototype_events).
        self._handed_off: set[str] = set()
        # Artifact path -> (version, content hash). The diff cursor for
        # `document` events (agent/reconcile.document_events). The hash answers
        # "did it change" and the ordinal answers "which revision is this" -- the
        # banner's close button remembers the version, so the value has to differ
        # per update. A restart empties it and the ordinal returns to 1; the cost of
        # that is recorded in reconcile.document_events.
        self._doc_versions: dict[str, tuple[int, str]] = {}
        # Whether the cursor above has been seeded once from the disk. See
        # `_seed_document_cursor`.
        self._docs_seeded = False
        self._current_session_id: str | None = None
        # The task draining the current turn's receive_response(). Outlives the
        # `run()` generator on purpose when a question parks the turn -- that is
        # what keeps the rest of the turn's messages for `run_answers`. See
        # _MessageReader.
        self._reader: _MessageReader | None = None
        self._on_file_published: (
            Callable[[str, str, str | None], None] | None
        ) = None

    # ---- plumbing ----

    # `_publish` used to live here. `report_stage` wrote the state file directly
    # and so bypassed the PostToolUse hook, which meant that tool had to be handed
    # a publisher of its own. Once it moved to the hook (agent/reconcile.py) the
    # state file started being published through the same path as every other
    # artifact -- the exception disappeared, and with it the publisher that existed
    # for the exception. Publishing is now `_on_post_tool_use` calling
    # `publish_file` directly.

    def _emit(self, event: AgentEvent) -> None:
        """Put an event on the turn queue.

        This was the `emit` sink handed to the custom tools. After those tools moved
        to hooks (the last being `submit_document` on 2026-08-21) it has no
        production caller, but it is kept because it states intent more clearly than
        appending to `_queue` directly -- the derived events are appended by
        `_emit_stage_events`, `_emit_document_events` and `_handoff_stop`
        themselves.
        """
        self._queue.append(event)

    def set_file_published_callback(
        self,
        callback: Callable[[str, str, str | None], None] | None,
    ) -> None:
        self._on_file_published = callback

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
            started = time.perf_counter()
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
            log_performance(
                _log,
                self._current_session_id, "connect", started, cold="true")
        return self._client

    async def _flush_transcript_mirror(self) -> bool:
        """Force this turn's mirrored transcript out to S3.

        Reaches into the SDK's batcher (`client._query`) rather than calling a
        public API, because there isn't one: `flush` is driven by the read loop
        on `result`/`close()`, and Discovery hits neither (see the call site).
        Guarded with getattr and a broad except precisely BECAUSE it is a
        private handle -- an SDK upgrade that moves or renames it must cost the
        durability of chat history, not the turn the user is watching.
        """
        started = time.perf_counter()
        batcher = getattr(getattr(self._client, "_query", None),
                          "_transcript_mirror_batcher", None)
        if batcher is None:
            log_performance(
                _log,
                self._current_session_id, "transcript_flush", started,
                available="false")
            return True
        try:
            await batcher.flush()
        except Exception:
            _log.exception("transcript mirror flush failed — "
                           "chat history for this turn may be lost")
            return False
        finally:
            log_performance(
                _log,
                self._current_session_id, "transcript_flush", started,
                available="true")
        return True

    async def interrupt(self) -> None:
        """Interrupt the turn in progress, keeping the work done so far.

        This follows proto/builder.py's interrupt as its pattern, with one
        difference: Discovery's pending question is also mirrored to S3
        (agent/pending_store.py). Clearing only the in-memory copy would let
        `GET /pending` restore a question nobody can answer, and the answers the user
        submits would resolve a future nobody is listening on.

        The order is load-bearing: clean up our own state first and touch the client
        last. That way a throwing client.interrupt() cannot leave a pending question
        behind.

        Idempotent: with no turn running it does nothing. The route calls this method
        based only on whether a session exists, so requests about an
        already-finished turn arrive as a matter of course.

        The decision is made on `_turn_active`/`_pending_question`, not on
        `_turn_token`. A turn parked on a question ends its run() generator with
        questions -> done, so `_release_turn` has already run and `_turn_token` is
        None -- while the CLI subprocess and the `_pending_question` future are still
        very much alive (see the "single-turn slot" section at the top of this file).
        Deciding on `_turn_token` would therefore misread exactly the state that
        needs interrupting (a parked question) as "nothing to interrupt".

        **`has_live_turn` is the third case in that family (2026-08-19).** A turn
        still generating with its consumer gone has `_turn_active` False and no
        parked question -- so on the two conditions above it read as "nothing to
        interrupt" and did nothing. Now that `run()` refuses to overwrite an
        in-flight turn, that state is **a dead end**: the user can reattach and watch
        but cannot stop it, and a new message is refused too. This path is their only
        way out after waking from sleep.
        """
        if self._client is None:
            return
        if (not self._turn_active and self._pending_question is None
                and not self.has_live_turn()):
            return
        if self._pending_question is not None and not self._pending_question.done():
            # The _on_can_use_tool awaiting this future is discarded along with
            # the turn.
            self._pending_question.cancel()
        self._clear_pending_state()
        await self._clear_pending_quietly()
        # A questions event left in the queue is a card nobody can answer --
        # letting it through puts a form on screen.
        self._queue = [e for e in self._queue if e.kind != "questions"]
        # Record that the turn was interrupted. This rides the existing `status`
        # kind rather than a new one so it reuses an event shape the frontend
        # already handles -- the frontend sees this marker and draws a one-line
        # "interrupted" note on that turn in the UI language. The marker exists only
        # in the live SSE queue: it never enters the transcript, so it is not
        # restored after a refresh.
        self._queue.append(AgentEvent(kind="status", text=INTERRUPTED_MARKER))
        await self._client.interrupt()

    async def _on_pre_tool_use(self, input_data, tool_use_id, context) -> dict:
        """Discovery's write-scope gate. The decision itself is in
        agent/discovery_guard.py.

        Why a hook: Discovery runs under bypassPermissions, so Write/Bash are
        auto-approved and never reach can_use_tool -- PreToolUse is the only gate the
        SDK offers (the rationale is in the factory's hooks comment above and in
        discovery_guard's header).

        **Passing through returns an empty dict.** Returning "allow" would also skip
        can_use_tool and kill the AskUserQuestion interception (see types.py's
        can_use_tool description).
        """
        name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input") or {}
        if name in _FILE_TOOLS:
            offender = write_denial(tool_input.get("file_path"), self._workspace)
            reason = (None if offender is None
                      else prompts.write_outside_docs(self._language, offender))
        elif name == "Bash":
            offender = bash_denial(tool_input.get("command"))
            reason = (None if offender is None
                      else prompts.build_command_refused(self._language, offender))
        else:
            # The matcher only catches the three above, but if the hook
            # configuration and this branch ever diverge it must pass through
            # quietly -- blocking an unknown tool stops the turn.
            return {}
        if reason is None:
            return {}
        # Log it: the refusal reason goes only to the model, so an operator needs
        # a separate way to see what was blocked.
        _log.warning("discovery gate denied %s: %s", name, offender)
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}

    def _file_question_round(self, rel: str) -> tuple[Any, str] | None:
        # Three possible returns: None (not applicable), ("unparsed", raw text),
        # or (QuestionFile, raw text). Parse failure is kept distinct from None
        # because silence here means the question is lost.
        """If the just-written `rel` is **a question round that should be asked**, return
        `(parse result, raw text)`.

        The raw text comes back too because the caller has to upload that file to S3
        (it must be in the source of record before the card is advertised), and there
        is no reason to read the same file twice.

        All four conditions must hold:

        1. **`looks_like_question_file` must be true** -- there is a line-initial
           `[Answer]:` slot and the name is not in `NEVER_QUESTION_FILES`.
           Inclusion is decided by content, not by name: upstream has broken its own
           naming convention (`{phase}-questions.md`) before -- it also put questions
           in `design-context.md` (see question_file_answers.py's header).
           Conversely, files whose **purpose upstream defines**, such as audit.md,
           are excluded by name.

           Until 2026-08-18 this gate had drifted from the write-back path (here a
           plain `"[Answer]:" in md`; there `^`-anchored). So when audit.md recorded a
           tag it matched only here, and the agent -- reasoning that "audit.md is not
           a question file, so I will remove that notation" -- **damaged its own audit
           record**, the very record `core-workflow.md:303` requires to be kept
           verbatim.
        2. `parse_ok`. If it does not parse, **pass over quietly.** The upstream
           format is not stable (2026-08-17: 1 of 8 files fell outside the parser), so
           blocking here would mean the question never reaches the screen at all --
           this has to degrade, not block.
        3. At least one question must have an empty answer. The agent re-reading
           answers and rewriting the file is normal behaviour, and must not trigger a
           re-ask.
        4. **Never ask the same set of unanswered questions twice.** The guard is
           keyed on the set of unanswered questions rather than on the file, because a
           question added after answers arrive (`### Clarification Question 2`) does
           need asking.

        This reads from disk: `Edit`/`MultiEdit`'s tool_input does not carry the
        full content, and PostToolUse runs after the write has completed.
        """
        from aipds.parsers.questions import parse_question_file
        try:
            md = (Path(self._workspace) / rel).read_text(encoding="utf-8")
        except OSError:
            return None
        if not looks_like_question_file(rel, md):
            return None
        qfile = parse_question_file(rel, md)
        if not qfile.parse_ok:
            # Do not pass this over quietly: now that AskUserQuestion is denied,
            # this is the complete loss of the question (the rationale is in
            # prompts.file_questions_unparsed).
            return "unparsed", md
        unanswered = tuple(q.ask or q.text for q in qfile.questions
                           if not (q.answer or "").strip())
        if not unanswered:
            return None
        if self._asked_question_sets.get(rel) == unanswered:
            return None
        self._asked_question_sets[rel] = unanswered
        return qfile, md

    def _emit_stage_events(self) -> None:
        """Read `aiplc-state.md` and emit `stage` only for the stages whose status
        changed.

        The only place that updates the cursor (`self._stage_status`) -- the hook path
        and the turn-boundary reconciliation both go through **the same function**, so
        both see the same diff. Two copies would mean one re-emitting what the other
        already emitted.
        """
        events, self._stage_status = reconcile.stage_events(
            reconcile.read_state(Path(self._workspace)), self._stage_status)
        for ev in events:
            self._queue.append(ev)

    def _emit_document_events(self) -> None:
        """Emit `document` only for artifacts whose content changed. Replaces the old
        `submit_document`.

        One function for the same reason as `_emit_stage_events`: the hook path and
        the turn boundary must go through **the same cursor**. Two copies would have
        the turn boundary re-announce a document the hook already announced, raising
        the update banner twice per document.
        """
        events, self._doc_versions = reconcile.document_events(
            Path(self._workspace), self._doc_versions)
        for ev in events:
            self._queue.append(ev)

    def _seed_document_cursor(self) -> None:
        """Mark the documents that **already existed** when the first turn started as
        "seen".

        `document` is derived from a workspace scan, so a turn starting with an empty
        cursor makes every existing document look new. This driver is what holds the
        cursor, so that state arrives on **a new driver's first turn** -- the first
        attach after a backend restart or a redeployment, at which point the tree is
        already populated (either a cold restore downloaded everything, or the local
        workspace survived from an earlier turn). A project with ten documents would
        announce ten times, and the update banner would point at whichever document
        came last in the scan rather than the one just written.

        **It runs once.** Seeding every turn would quietly swallow documents changed
        between turns (a write from another instance, or restored content that
        differs). After the first turn, every change is announced.

        **Why here and not in `__init__`** comes down to that restore: the workspace
        is empty when the driver is constructed and is filled in just before the
        runner calls `run()`. Seeding at construction time would see nothing, and then
        the first turn would announce everything all over again.
        """
        if self._docs_seeded:
            return
        self._docs_seeded = True
        _, self._doc_versions = reconcile.document_events(
            Path(self._workspace), self._doc_versions)

    def _reconcile_turn(self) -> None:
        """Align the workspace and the UI before the turn ends. Called by `_pump`.

        This recovers the changes the hook could not see (via Bash) and the events it
        missed (a dropped batch). A handoff is only announced here, **the turn is not
        cut** -- the turn is already ending, and there is nowhere here to return
        `continue_: False` from anyway.

        Exceptions are swallowed. Reconciliation is a backstop, and a backstop that
        fails the turn is not a backstop but a new cause of failure
        (runner._sync_abandoned_turn makes the same judgement).
        """
        try:
            self._emit_stage_events()
        except Exception:
            _log.exception("stage reconciliation failed")
        try:
            events, self._handed_off = reconcile.prototype_events(
                Path(self._workspace), self._handed_off)
            for ev in events:
                self._queue.append(ev)
        except Exception:
            _log.exception("prototype handoff reconciliation failed")
        try:
            self._emit_document_events()
        except Exception:
            _log.exception("document reconciliation failed")

    def _handoff_stop(self, rel: str) -> dict | None:
        """Confirm a `build-instructions.md` write as a handoff and end the turn.

        `None` means it was not confirmed, and the turn continues. The case where it
        is not confirmed is a spec that does not exist yet: the Prototypes tab builds
        its card from the spec (`routes/prototypes.py` assembles the list via
        `layout.discover`), so build instructions alone leave the user looking at an
        empty tab. That is why the old `handoff_prototype` checked for the spec, and
        that check moved into `reconcile.handed_off`.

        **The turn ends here for the same reason as on a question file.** Discovery's
        work finishes at this point and the next action belongs to the user (building
        in the Prototypes tab). Without ending it, the agent carries on into upstream
        Step 4 (Iterate) or asks for credentials -- both measured failures. So the
        `stopReason` **names** the next action: prompts.py's header states the
        principle that given only a reason, the model improvises.
        """
        events, cursor = reconcile.prototype_events(
            Path(self._workspace), self._handed_off)
        if not events:
            # An already-announced handoff passes quietly (the case where the
            # build instructions were rewritten): no second card, and the turn is
            # not cut again.
            if reconcile.prototype_id_for(rel) not in self._handed_off:
                _log.warning("build instructions at %s have no prototype spec "
                             "yet — not handing off", rel)
            return None
        self._handed_off = cursor
        for ev in events:
            self._queue.append(ev)
        slug = reconcile.prototype_id_for(rel) or ""
        _log.info("prototype handed off from %s (slug=%s)", rel, slug)
        return {"continue_": False,
                "stopReason": prompts.prototype_handoff_stop(self._language, slug)}

    async def _on_post_tool_use(self, input_data, tool_use_id, context) -> dict:
        name = input_data.get("tool_name", "")
        if name not in _FILE_TOOLS:
            return {}
        fp = (input_data.get("tool_input") or {}).get("file_path", "")
        rel = _rel(fp, self._workspace)
        if rel is None:
            self._queue.append(AgentEvent(
                kind="status", text="file outside workspace ignored"))
            return {}
        # **Publish before advertising.** A UI that receives `file_changed` comes
        # straight back to read that document (WorkspaceDocPanel), and every read
        # path goes to the source of record (S3). Deferring that publish to the end
        # of the turn produces "it says it was written but it is not in the list /
        # selecting it shows nothing / it appears briefly and vanishes" -- which is
        # exactly what happened on 2026-08-18, where all 16 measured S3 timestamps
        # fell within one second of the turn ending.
        #
        # A failure here does not kill the turn (publish_file swallows it). The
        # end-of-turn batch sync is still the backstop.
        await publish_file(
            self._s3,
            Path(self._workspace),
            rel,
            on_published=self._on_file_published,
        )
        # A question file is an artifact too: the document panel updates from this
        # event, so it is emitted first, independently of the branches below.
        self._queue.append(AgentEvent(kind="file_changed", path=rel))
        # The stage badges are derived by **parsing** the state file (replacing
        # the old `report_stage` tool -- the full story is in agent/reconcile.py's
        # header). The upstream rules require the agent to keep this file current
        # itself (common/workflow-changes.md, discovery/prototype-validation.md
        # Step 10), so the signal is already in the workspace.
        #
        # This reads **the disk**, not the hook payload: an Edit carries only a
        # patch, so the payload cannot tell us the file's full text.
        # `_file_question_round` made the same choice for the same reason.
        if rel == reconcile.STATE_KEY:
            self._emit_stage_events()
        # The document update banner and the document panel's activeDoc are
        # **derived** from artifact writes (replacing the old `submit_document`
        # tool, which was instructed to be called for every document and mostly was
        # not -- the frontend recorded the measurement at
        # useWorkspaceStream.ts:177: "the agent creates most documents with
        # file_write alone, without submit_document"). The same class of silence as
        # the stage badges and the handoff.
        if reconcile.is_document(rel):
            self._emit_document_events()
        # The prototype handoff: the moment Step 3's final artifact is written.
        # Unlike the old `handoff_prototype` tool, the model cannot forget this --
        # the tab being pointed at zero times in keumkang-v5 (2026-08-17) is why
        # that tool was created, and the tool merely postponed the same "silent
        # unless called" failure by one step.
        if reconcile.prototype_id_for(rel) is not None:
            stop = self._handoff_stop(rel)
            if stop is not None:
                return stop
        if not _file_questions_enabled():
            return {}
        round_ = self._file_question_round(rel)
        if round_ is None:
            return {}
        qfile, md = round_
        if qfile == "unparsed":
            # Do not repeat the same note for the same content (which would loop
            # inside the turn). Report again when the content **changes** -- that is
            # the case where the file was rewritten and is still wrong, and staying
            # silent there loses the question again.
            if self._unparsed_noted.get(rel) == md:
                _log.warning("file questions: %s still does not parse (already "
                             "reported this content)", rel)
                return {}
            self._unparsed_noted[rel] = md
            _log.warning("file questions: %s has [Answer] tags but no parsable "
                         "question — telling the agent", rel)
            return {"hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": prompts.file_questions_unparsed(
                    self._language, rel)}}
        # `interrupt_id` is the empty string: there is no parked can_use_tool
        # future, so what the answers return to is **the file**, not the turn. The
        # frontend tells the two apart by `file` and sends answers to
        # PUT /projects/{pid}/questions/{name}.
        self._queue.append(AgentEvent(kind="questions", payload=json.dumps(
            {"interrupt_id": "", "file": rel, "questions": qfile},
            ensure_ascii=False, default=lambda o: o.model_dump())))
        # **Upload the file to S3 first -- do not wait for the end-of-turn sync.**
        #
        # Measured failure, 2026-08-17: in a real turn the card appeared but
        # `GET /pending` returned `file=None` and submitting answers returned 404.
        # The hook reads the local file, but that file only reaches S3 (the source of
        # record) at the runner's done/error sync. The gap between the two is a
        # window, and inside it `pending()` cannot find the file the marker points
        # at, while answer submission 404s because `runner.read_file` reads S3.
        #
        # The moment the card is advertised, the file has to be in the source of
        # record already. The content is already in hand here, so uploading here is
        # the cheapest and most certain place (one put per round). The runner's sync
        # re-uploading the same content later is harmless.
        try:
            # The file was already published above (publish_file) -- every artifact
            # goes through the same path, so no question-file-specific upload is
            # kept here. The marker comes **after** it: reversing the order leaves a
            # window where the marker points at a file not yet in the source of
            # record.
            await save_pending_file(self._s3, file=rel)
        except Exception:
            # The live card is already in the queue: even on failure this turn's
            # question still appears on screen, and only refresh-restore and answer
            # submission are lost. Killing the turn would be worse.
            _log.exception("publishing the open question file failed")
        # The hook does not wait for a human -- it just stops the turn
        # immediately. Measured (2026-08-17): `continue_: False` ends with
        # `terminal_reason='hook_stopped'` and `is_error=False`, so `_translate`
        # already treats it as a normal `done`, and any tool calls batched after it
        # in the same message do not run (which is the intent -- it stops the model
        # rebuilding the same questions through AskUserQuestion).
        _log.info("file questions: asking %d question(s) from %s",
                  len(qfile.questions), rel)
        return {"continue_": False,
                "stopReason": prompts.file_questions_stop(self._language, rel)}

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
        # With the file path enabled, this tool is not used. A **denial**, not a
        # removal: simply dropping the interception would make the question vanish
        # silently the moment the model called the tool (absent from the screen and
        # the chat alike). A denial comes with an alternative.
        #
        # Why there is a single switch: with both paths alive at once the agent
        # writes the file (the hook shows a card) and then also calls this tool, so
        # the same question appears twice.
        if _file_questions_enabled():
            _log.info("AskUserQuestion denied — file questions are the only path")
            return PermissionResultDeny(
                message=prompts.ask_user_question_denied(self._language))
        import uuid
        # A payload that arrived as a string is unpacked into a list here: passed
        # through unnormalised, question_file_from_sdk would iterate the string
        # character by character and blow up with an AttributeError, and that
        # exception escapes this callback and kills the turn. The three observed
        # rejections were blocked by the CLI **before** this callback, so they are
        # not recoverable here (the rationale is in normalize_sdk_questions'
        # docstring).
        sdk_questions = normalize_sdk_questions(input_data.get("questions"))
        # The join key for restore. The SDK gives this callback a tool_use_id
        # (non-empty is a wire-protocol guarantee) and the transcript's tool_result
        # carries the same id, so keying the answer record on it lets restore join
        # exactly, with no guessing from order or timestamps (see
        # agent/answer_store.py's header). It is read through getattr because a test
        # double calling this callback directly may pass an abbreviated context.
        tool_use_id = getattr(context, "tool_use_id", None) or ""
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
                message=prompts.question_payload_rejected(self._language, str(e)))
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
        # The record used by restore. Written **before** returning
        # updated_input: this return resumes the turn and the next tool starts
        # running soon after, so putting it later makes it hard even from the logs
        # to tell which round's record went missing when it fails.
        await self._save_answers_quietly(tool_use_id, iid, qfile, answers)
        # The answers are also planted in the question file's `[Answer]:` slots,
        # because the ai-plc workflow reads those slots -- the rationale, and why the
        # matching is textual, are in agent/question_file_answers.py's header. Being
        # here (just before the return) is for the same reason as
        # _save_answers_quietly: this return resumes the turn and the next tool
        # starts soon after, so placing it later risks the file landing after the
        # next stage reads it. record_answers swallows every failure and returns an
        # empty list.
        for rel in record_answers(self._workspace, sdk_questions, answers):
            await self._mirror_question_file_quietly(rel)
            self._queue.append(AgentEvent(kind="file_changed", path=rel))
        return PermissionResultAllow(updated_input={
            "questions": sdk_questions,
            "answers": sdk_answers,
        })

    async def _mirror_question_file_quietly(self, rel: str) -> None:
        """Also upload the written-back question file to S3. Never kills the turn.

        **Why it is needed.** record_answers writes the local workspace file, but
        `runner.read_file` reads from S3 (runner.py:55) -- and S3 is what the
        artifacts panel on screen and the next stage actually see. Writing only
        locally leaves the answers **invisible** until the turn ends and
        `_sync_workspace_to_s3` runs.

        That delay is also a window for losing them: `_restore_workspace_from_s3` runs
        at the start of every turn and its comment says S3 wins unconditionally
        (runner.py:79). If a turn is abandoned without a terminal event
        (`_sync_abandoned_turn` is best-effort), the next turn overwrites the local
        copy with S3's empty file and the answers are gone.

        The local file is re-read before uploading so that exactly the bytes on disk
        go up. Carrying a separate in-memory copy would let the two drift.
        """
        try:
            content = (Path(self._workspace) / rel).read_text(encoding="utf-8")
            await self._s3.put(rel, content)
        except Exception:
            # The local file is already updated and the end-of-turn sync gives a
            # second chance -- killing the turn here would lose the answers the user
            # just submitted.
            _log.exception("question-file S3 mirror failed: %s", rel)

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

    async def _save_answers_quietly(self, tool_use_id: str, iid: str,
                                    qfile: dict, answers: dict) -> None:
        """Record the submitted answers to S3. Never fails the turn.

        The same judgement as the pending mirror: this record is a convenience for
        restore, and killing the turn the user just answered because of an S3 hiccup
        would be worse. Without the record, restore falls back to the same path as an
        older session (the CLI's own prose wording).

        It is skipped when there is no tool_use_id: that value is the join key for
        restore, so a record written without it cannot be attributed to a round and is
        unusable by the reader.
        """
        if not tool_use_id:
            _log.warning("no tool_use_id for answer record — skipped")
            return
        try:
            await save_answers(self._s3, tool_use_id=tool_use_id,
                               interrupt_id=iid, questions=qfile,
                               answers={str(k): str(v) for k, v in answers.items()})
        except Exception:
            _log.exception("answer record S3 persist failed")

    async def _clear_pending_quietly(self) -> None:
        try:
            await clear_pending(self._s3)
        except Exception:
            _log.exception("pending-question S3 clear failed")

    # ---- message translation + the turn pump ----

    def _translate(self, msg, reader: "_MessageReader | None" = None) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        tname = type(msg).__name__
        if tname == "SystemMessage" and getattr(msg, "subtype", "") == "mirror_error":
            # A transcript batch failed to reach S3. The SDK does NOT retry it
            # (at-most-once), so this message is the only signal there is -- and
            # we were dropping it whole. That is what made the empty-history bug
            # so slow to pin down: with no log either way, "the write failed"
            # and "the write was never attempted" look identical from outside,
            # and it turned out to be the second one.
            #
            # Logged, not surfaced: durable history is secondary data, and there
            # is nothing a workshop attendee can do about an S3 error mid-turn.
            _log.warning("transcript mirror failed (history for this turn may "
                         "be lost): %s", getattr(msg, "error", "unknown"))
            return events
        if tname == "AssistantMessage":
            for block in getattr(msg, "content", []):
                btype = type(block).__name__
                if btype == "TextBlock":
                    events.append(AgentEvent(kind="message", text=block.text))
                elif btype == "ToolUseBlock":
                    # Send what it did, not just that it did something: a bare
                    # `Read` misses the whole point of the trace (see the tool_trace
                    # module header). Only the value is sent; the icon and separator
                    # in `🔍 Read · …` are drawn by the frontend in the UI
                    # language.
                    detail = tool_detail(block.name, getattr(block, "input", None))
                    # detail goes into the dedupe key. Collapsing on the name alone
                    # squashes three consecutive Reads into one `Read` line -- even
                    # when they are different files.
                    key = (block.name, detail)
                    if key != self._last_status:
                        self._last_status = key
                        events.append(AgentEvent(
                            kind="status", text=block.name,
                            payload=(json.dumps({"detail": detail},
                                                ensure_ascii=False)
                                     if detail else None)))
        elif tname == "ResultMessage":
            # The CLI reports turn failure HERE, not by raising: `is_error`
            # true means the turn ran but ended in failure (a Bedrock 429/500/
            # 529, a wedged tool, an aborted stream). Ignoring it is what let
            # a failed PR/FAQ turn render as a normal answer -- the CLI writes
            # its own "API Error: ..." prose into an AssistantMessage, which
            # `message` above relays as ordinary text, and then this branch
            # said `done`. The frontend closes the stream on `done`
            # (sse.ts:29) and never takes its error branch, so the user saw
            # English error prose glued to Korean output and the agent
            # retrying the same step -- with nothing in our logs at all,
            # because every field describing the failure was dropped right
            # here.
            #
            # `api_error_status` is the field worth logging: it distinguishes a
            # transient 429/529 (same request succeeds on retry) from a 500
            # (does not). Logged rather than shown -- an HTTP status is not
            # something a workshop attendee can act on.
            if getattr(msg, "is_error", False):
                # `is_error` alone conflates two different things. The CLI sets
                # it for a genuine failure (Bedrock 429/500/529, a wedged tool)
                # AND for a turn the user cancelled via our own interrupt() --
                # `terminal_reason` is what tells them apart
                # (claude_agent_sdk/types.py:1249-1257 documents
                # "aborted_streaming"/"aborted_tools" as the cancelled-turn
                # values). Without this check, pressing the interrupt button
                # showed the interrupted status line (Task 5's, correct) stacked
                # with "this turn failed" (a lie) -- the real-CLI probe that
                # unit tests missed, because the fake SDK never scripted
                # is_error=True together with an aborted terminal_reason.
                #
                # `terminal_reason is None` (older CLIs that predate this
                # field) falls through to the failure path below, same as
                # before -- `is_error` is the only signal we have then.
                terminal_reason = getattr(msg, "terminal_reason", None)
                interrupted = terminal_reason in _INTERRUPTED_TERMINAL_REASONS
                if not interrupted:
                    _log.error(
                        "claude CLI reported a failed turn: api_error_status=%s "
                        "subtype=%s terminal_reason=%s errors=%s",
                        getattr(msg, "api_error_status", None),
                        getattr(msg, "subtype", None),
                        terminal_reason,
                        getattr(msg, "errors", None),
                    )
                    # Recorded on the reader, NOT returned as an `error` event.
                    # `_pump` owns the terminal event and emits exactly one, always
                    # last (its invariant 1); returning a second terminal here
                    # would break that. The flag makes `_pump` emit `error`
                    # instead of `done` after its drain.
                    #
                    # On the READER rather than on `self` because this is per-turn
                    # state: the reader's lifetime IS the turn's, so a failed turn
                    # cannot leak its verdict into the next one. A driver-level
                    # flag would have to be reset by hand on every entry path
                    # (`_stream`, `_continue_after_answers`) and would be wrong the
                    # moment one of them forgot.
                    if reader is not None:
                        reader.failed = True
                # An interruption is not logged at error level: it is the normal
                # result of a button the user just pressed, not a failure we need to
                # find. Logging it as an error would pollute the workshop logs on
                # every interruption and add noise when hunting a real failure
                # (429/500/529, a wedged tool).
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
        # The identity of the holder that started this pump. If a reattach
        # preempts the slot (`_acquire_turn(preempt=True)`) this value is no longer
        # the holder, and the check below turns that into **a real interruption**.
        #
        # Swapping the token but leaving this loop running is not enough: the old
        # consumer's generator is merely paused at a `yield`, not dead, so if the
        # client's TCP revives and reads again, two consumers split the same
        # `outbox`. Measured (in tests): after preemption the old consumer took
        # `sentence 2`, `questions` and `done`, and they vanished from the
        # reattached screen.
        #
        # **The check goes before the `yield`.** The ownership rule above ("an item
        # leaves its place only after the consumer has received it") has to keep
        # holding, so stopping here leaves the remaining items un-popped and owned by
        # `outbox`/`_queue` for the next pump to relay -- preemption **hands frames
        # over** rather than dropping them.
        pump_token = self._turn_token

        def owns_turn() -> bool:
            return self._turn_token is pump_token

        def translate_into_outbox() -> None:
            """Move messages inbox -> outbox, translating. Never yields.

            Wholly synchronous, so a message is never in neither place: it
            leaves `inbox` and enters `outbox` with no suspension in between.
            `outbox` lives on the reader, so anything still there when this
            generator dies belongs to the next pump over the same reader.
            """
            nonlocal ended
            while reader.inbox and not ended:
                for ev in self._translate(reader.inbox.pop(0), reader):
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
                    if not owns_turn():
                        return          # Preempted -- remaining items stay owned
                                        # and are handed to the next pump
                    ev = queue[0]
                    yield ev
                    # Reached only if the consumer came back for the next item,
                    # i.e. it really received this one.
                    if queue and queue[0] is ev:
                        queue.pop(0)
                    asked = asked or ev.kind == "questions"

        while True:
            if not owns_turn():
                return                  # Preempted -- see the pump_token comment
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
        # Turn-boundary reconciliation -- recover from the workspace whatever the
        # hook missed.
        #
        # **Why the hook alone is not enough.** PostToolUse is attached only to
        # `Write|Edit|MultiEdit`, so if the agent edits a file through Bash
        # (`python3 -c`, `sed`, a redirection) the hook never sees it.
        # discovery_guard.py's header already records the same limitation: Bash is
        # arbitrary code execution, so no denylist covers every path, and adding Bash
        # to the matcher would still not reveal which file a command targets.
        #
        # So the final authority is neither a per-moment declaration (a tool) nor a
        # per-write observation (the hook) but **consistency at the boundary**. One
        # read of the disk here covers Bash bypasses, missed hooks and dropped
        # batches alike. It also catches the lost `report_stage` from test123456 on
        # 2026-08-18.
        #
        # **Being before the terminal emit is the point.**
        # `frontend/lib/api/sse.ts:29` closes the EventSource on `done`, so events
        # after it never reach the screen (invariant 1 above). Queuing here lets the
        # drain loop below emit them ahead of the terminal event.
        #
        # Nothing already emitted is emitted again: both cursors (`_stage_status`,
        # `_handed_off`) are shared with the hook path, so reconciliation usually
        # does nothing at all. That is the normal case.
        #
        # It also runs on `asked` (a turn that ended in a question). That path is
        # exactly where the loss was observed: the question file write cut the turn
        # and the calls batched alongside it disappeared.
        self._reconcile_turn()
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
        # Still exactly one terminal event, still last -- only its KIND now
        # depends on whether the CLI reported the turn as failed
        # (`_translate`'s ResultMessage branch). `error` rather than a raise
        # because the turn is over and everything it produced has already been
        # relayed: raising here would send the caller down `_stream`'s except
        # arm, which relays the queue a second time.
        #
        # Deliberately NOT reset here. A failed turn's verdict dies with the
        # reader, and `_continue_after_answers` pumps the SAME reader -- so if
        # the question turn already failed, its continuation stays failed
        # rather than reporting success.
        # Push this turn's transcript to S3 before the terminal event, because
        # for the caller `done` means the turn is over -- the SSE response
        # closes and nothing else will run on our behalf.
        #
        # The SDK's batched mode normally flushes on `result` or `close()`, and
        # a question turn reaches neither (`asked` leaves the CLI parked while
        # the client stays cached). This explicit boundary keeps batching from
        # weakening durability.
        reader.transcript_flushed = await self._flush_transcript_mirror()
        if reader.failed:
            yield AgentEvent(kind="error",
                             text=prompts.turn_failed(self._language))
        else:
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

    def _acquire_turn(self, *, preempt: bool = False) -> object | None:
        """Claim the turn slot; None if one is already running. The token
        identifies THIS turn so a rejected caller cannot release the slot the
        live turn is holding.

        `preempt=True` takes the slot regardless and **never returns None**. Only
        the reattach path uses it, and the policy it implements is "the request
        that just arrived wins": a person watches one screen at a time, so a new
        connection is the real user and whatever held the slot is presumed
        stale.

        This is what makes reattach possible at all. The flag says "a consumer
        exists" (see `has_live_turn`), and a suspended laptop never sends a FIN
        -- so the generator of the consumer that went away is still alive at its
        `yield`, still holding the slot, exactly when reattach is needed. Before
        preemption the reattach request was refused with "turn already in
        progress" (measured on the deployed instance 2026-08-19, where that text
        reached the user as agent speech).

        Issuing a NEW token is the whole eviction mechanism: `_release_turn` is
        token-guarded, so when the stale holder finally closes, its release is a
        no-op and cannot free the slot underneath the reattached consumer.
        """
        if self._turn_active and not preempt:
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
        turn_started = time.perf_counter()
        first_sdk_event_logged = False
        first_text_logged = False
        reader_for_turn: _MessageReader | None = None
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
            query_started = time.perf_counter()
            await client.query(text)
            log_performance(
                _log,
                self._current_session_id, "query", query_started)
            reader_for_turn = _MessageReader(
                client.receive_response().__aiter__())
            self._reader = reader_for_turn
            async for ev in self._pump(reader_for_turn):
                if not first_sdk_event_logged:
                    log_performance(
                        _log,
                        self._current_session_id,
                        "first_sdk_event",
                        turn_started,
                    )
                    first_sdk_event_logged = True
                if not first_text_logged and ev.kind == "message" and ev.text:
                    log_performance(
                        _log,
                        self._current_session_id, "first_text", turn_started)
                    first_text_logged = True
                yield ev
        except Exception:
            _log.exception("claude sdk turn failed")
            async for ev in self._relay_queue():
                yield ev
            yield AgentEvent(kind="error", text="agent turn failed")
            return
        finally:
            if reader_for_turn is None or not reader_for_turn.transcript_flushed:
                await self._flush_transcript_mirror()
            log_performance(
                _log,
                self._current_session_id, "driver_turn_total", turn_started)

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
        reader.transcript_flushed = False
        try:
            async for ev in self._pump(reader):
                yield ev
        except Exception:
            _log.exception("claude sdk answer turn failed")
            async for ev in self._relay_queue():
                yield ev
            yield AgentEvent(kind="error", text="agent turn failed")
            return
        finally:
            if not reader.transcript_flushed:
                await self._flush_transcript_mirror()

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
        # submitted "i-STALE" -> prompt said `- NEW question -> proceed`, record
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
        prompt = prompts.answers_resumed(
            self._language, "\n".join(lines),
            json.dumps({"interrupt_id": interrupt_id, "answers": answers},
                       ensure_ascii=False))
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
        abandoned.

        Writing them every turn helps the language directive too: even if it is no
        longer in the workspace, the next turn puts it back."""
        started = time.perf_counter()
        try:
            place_rules(self._workspace, self._rules_dir, self._language)
            return True
        except Exception:
            _log.exception("rule placement failed")
            return False
        finally:
            log_performance(
                _log,
                self._current_session_id, "rules", started)

    async def run(self, text: str, session: dict) -> AsyncIterator[AgentEvent]:
        """Contract: runner.py:129 calls this."""
        self._current_session_id = session.get("session_id")
        if not self._place_rules():
            yield AgentEvent(kind="error", text="agent turn failed")
            return
        # Mark the documents already in the workspace at this moment as "seen", so
        # a new driver's first turn does not announce every existing document as an
        # update.
        self._seed_document_cursor()
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
                yield AgentEvent(kind="message",
                                 text=prompts.answer_first(self._language))
                if self._pending_payload is not None:
                    yield AgentEvent(kind="questions",
                                     payload=self._pending_payload)
                yield AgentEvent(kind="done")
                return
            # **Stop a new turn from overwriting an abandoned one (2026-08-19).**
            # The slot is released the moment the consumer disappears (see the
            # comment above), so control can reach here -- and at that point
            # `_stream` would use `_retire_reader()` to **cancel the in-flight
            # turn's reader** and overlay a `query()` onto the same CLI session. That
            # function's own comment is written on the premise of "the turn nobody
            # will relay" -- a premise that stopped being true once the reattach path
            # (`run_live`) existed.
            #
            # Placing this **after** the parked-question short circuit matters: that
            # reader is alive too (has_live_turn is true), so putting it first would
            # turn the path that re-shows the question form into this refusal.
            if self.has_live_turn():
                _log.info("refusing a new turn: one is still streaming with no "
                          "consumer — the caller should reattach")
                yield AgentEvent(kind="error", text="turn already in progress")
                return
            async for ev in self._stream(text, session):
                yield ev
        finally:
            self._release_turn(token)

    def has_live_turn(self) -> bool:
        """A turn is still streaming with nobody consuming it.

        **Why `_turn_active` cannot tell you this.** That flag says whether a
        *consumer* is attached. When the SSE drops, `run()`'s finally clears it
        immediately -- which is intended (a reattaching browser must not be bounced
        with "turn already in progress"). But the CLI turn itself keeps running and
        `_MessageReader` keeps reading. Distinguishing those two is what this function
        is for.

        Why `ended` is also consulted: a parked question's reader is alive but not
        finished (hence True), while a normally completed turn's reader either has a
        finished task or is `ended`.
        """
        reader = self._reader
        return (reader is not None and not reader.task.done()
                and not reader.ended)

    async def run_live(self) -> AsyncIterator[AgentEvent]:
        """Reattach to a turn in progress -- without a new `query()`.

        **Why it is needed (2026-08-19).** When the user's machine sleeps or the
        screensaver kicks in, the network drops and the SSE dies. Turns run 2.5-5.6
        minutes, which collides head-on with default screensaver timeouts (5-10
        minutes). What is lost in that moment is **only the screen**: the reader keeps
        reading (`has_live_turn`), files reach S3 the instant PostToolUse writes them,
        and the abandon path flushes the transcript. But there was **no way to look at
        that in-flight turn again** -- `GET /pending` only covers a turn parked on a
        question, `GET /history` only works after it finishes, and
        `GET /events?turn=` requires a single-use 60-second handle created by a POST.

        **It reuses `_continue_after_answers` as-is.** That function's body already is
        nothing more than "relay the rest of the in-flight turn through the same
        reader" -- resolving the answers (`fut.set_result`) happens in `run_answers`
        **before** it calls that function. So reattaching and resuming-after-answers
        are the same action, differing only in what happens beforehand.

        With no turn to attach to it yields a single `done`, not an error. A user
        returning late to a turn that has since finished is the normal path, and the
        screen is restored from `GET /history` in that case.
        """
        # **This path preempts** -- it does not refuse.
        #
        # It used to take the slot with `_acquire_turn()` and emit "turn already in
        # progress" on failure, reasoning that "two tabs reading the same outbox
        # means one of them loses messages". That reasoning is correct, but **most of
        # what this gate blocked was not a second tab: it was a dead first tab.** A
        # client that went to sleep never sends a FIN, so the departed consumer's
        # generator stays alive holding the slot -- which is precisely the moment a
        # reattach is needed. Measured (2026-08-19): the refusal text appeared on the
        # user's screen as agent speech.
        #
        # The policy is "the request that just arrived wins". A person looks at one
        # screen at a time, so the new connection is the real user. The old consumer
        # is evicted by the issuing of a new token (the `_owns_turn` check below turns
        # that into a real interruption).
        token = self._acquire_turn(preempt=True)
        try:
            if not self.has_live_turn():
                yield AgentEvent(kind="done")
                return
            async for ev in self._continue_after_answers():
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
        self._current_session_id = session.get("session_id")
        if not self._place_rules():
            yield AgentEvent(kind="error", text="agent turn failed")
            return
        # Mark the documents already in the workspace at this moment as "seen", so
        # a new driver's first turn does not announce every existing document as an
        # update.
        self._seed_document_cursor()
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
        covers both a same-process refresh and a backend restart.

        Third, it looks at the **file question round**. That round leaves no parked
        future behind (the PostToolUse hook ended the turn), so neither of the two
        paths above has anything. It comes last because a value in either of those
        means there is a live future waiting for an answer, and in that case the turn
        only resumes once that question is answered.
        """
        if self._pending_payload is not None:
            return self._pending_payload
        data = await load_pending(self._s3)
        if data is not None:
            return json.dumps({"interrupt_id": data["interrupt_id"],
                               "questions": data["questions"]},
                              ensure_ascii=False)
        return await self._pending_from_file()

    async def _pending_from_file(self) -> str | None:
        """Re-read the open question file from S3 and rebuild the card.

        **It reads S3, not the local workspace.** The local copy is only restored at
        the start of a turn (runner.py's `_restore_workspace_from_s3`), so a
        `GET /pending` right after a restart would look at an empty directory. S3 is
        the only truth.

        None when there are no unanswered questions -- that is this round's
        end-of-life signal, and it is why the answer-submission path does not have to
        delete anything.
        """
        rel = await load_pending_file(self._s3)
        if rel is None:
            return None
        from aipds.parsers.questions import parse_question_file
        try:
            md = await self._s3.get(rel)
        except FileNotFoundError:
            # The file is gone (deleted, or moved). Restore is a convenience, so
            # give up quietly.
            return None
        qfile = parse_question_file(rel, md)
        if not qfile.parse_ok:
            return None
        if not any(not (q.answer or "").strip() for q in qfile.questions):
            return None
        return json.dumps({"interrupt_id": "", "file": rel, "questions": qfile},
                          ensure_ascii=False,
                          default=lambda o: o.model_dump())

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
