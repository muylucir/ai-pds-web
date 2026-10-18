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
from aipds.agent.delta_buffer import WhitespaceBoundaryBuffer
from aipds.agent.discovery_guard import (NOTEBOOK_TOOL, WRITE_TOOLS,
                                              audit_overwrite_denial, bash_denial,
                                              settings_denial, tool_write_path,
                                              web_owned_denial, write_denial)
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
from aipds.agent_home import copy_config
from aipds.cli_settings import cli_context_env, cli_debug_args
from aipds.context_usage import ContextMeter, wants_sample
from aipds.models import AgentEvent
from aipds.pathsafe import workspace_relative as _rel
from aipds.agent import tool_input_draft
from aipds.performance import log_performance
from aipds.s3store import S3StoreLike
from aipds.tool_trace import tool_detail
from aipds.transcript_restore import MirrorOnlyStore
from aipds.transcript_restore import restore as restore_transcript
from aipds.workspace_sync import publish_file

_log = logging.getLogger("aipds.agent")

#: File-writing tools. Owned by discovery_guard -- PostToolUse (observation) and
#: PreToolUse (blocking) must see the **same** set. Keeping two copies lets a tool
#: get added to only one of them, leaving an "observed but not blocked" hole.
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
# (claude_agent_sdk/types.py:1249-1257): a turn cut off by interrupt() arrives as one
# of these two, whether it was streaming or running a tool. They are collected here
# rather than hard-coded in the branch so that, if the SDK adds a third value, this
# one list is the only thing to change.
_INTERRUPTED_TERMINAL_REASONS = frozenset({"aborted_streaming", "aborted_tools"})

#: The text of the `status` event that marks an interrupted turn. **A machine signal,
#: not human-readable copy** -- frontend/lib/useWorkspaceStream.ts compares this value
#: to set its interrupted flag, and the on-screen label ("중단됨"/"Interrupted") is
#: drawn by the frontend in the UI language. proto/builder.py uses the same value -- if
#: the two drivers diverge, the frontend behaves differently depending on the path.
#:
#: Note that the reason it is language-neutral differs from the approval marker
#: (frontend/lib/approvalMarker.ts): that one goes to the agent and stays in the
#: transcript as a user bubble, so it must be a word in the project language. This
#: marker lives only in the live SSE queue and nobody reads it.
INTERRUPTED_MARKER = "interrupted"

#: The text of the `status` events saying the model entered/left a thinking block. The
#: same kind of **machine signal** as INTERRUPTED_MARKER -- the frontend compares the
#: value to set its state and draws the label itself in the UI language. It must not
#: be rendered as a trace line.
#:
#: **Why a span rather than text (measured 2026-09-04, Bedrock · claude-opus-5).**
#: Even with `--thinking-display summarized`, `thinking_delta` arrives with 0 chars and
#: only `signature_delta` carries data -- thinking **text** does not exist on this path.
#: What exists is the moment a thinking block opens and closes, and that coincides
#: exactly with the longest and most unsettling silence in a turn. So only the signal
#: is sent.
#:
#: **Why not a new kind.** It is a signal that exists only on the live SSE stream, so
#: there is no reason to touch the AgentEvent contract or history restore
#: (session_history). interrupt already reuses status on the same judgment.
THINKING_MARKER = "thinking"
THINKING_DONE_MARKER = "thinking-done"

# Discovery runs with a human in the loop watching the chat, unlike the
# unattended prototype build -- but AskUserQuestion is still routed through
# can_use_tool (see _on_can_use_tool below) and every other tool must execute
# without a separate approval round-trip, since the UI's only approval
# mechanism IS the questions card. Kept as the same default as builder.py's
# DEFAULT_PERMISSION_MODE for the same reason: any mode that can prompt stalls
# the turn with no operator to answer the CLI-level prompt.
DEFAULT_PERMISSION_MODE = "bypassPermissions"

#: Whether a question file is asked **as the file itself** from the PostToolUse hook. **On by default.**
#:
#: This is the only question path -- when on, AskUserQuestion calls are denied and the
#: denial message sends the agent back to write a question file. Enabling both at once
#: puts the same question on screen twice (the agent writes the file, the hook shows the card, and then it calls the tool too).
#:
#: **Why on by default.** Rebuilding the questions written to a file as this tool's
#: input damaged 15 of 19 questions measured (79%) -- 11 Hangul character substitutions
#: ("푸로토하이프가 … 어느 쉘입니까?" -- a garbled "which shell is the prototype …?" --
#: appeared on screen), and 4 answers lost to abbreviation. Reading the file as is makes
#: that class of failure disappear.
#:
#: Flipped on 2026-08-17 after one full round with a real Discovery turn: the hook showed
#: the card, the turn stopped, the answers were recorded in the file, and **on the next
#: turn the model read those answers and continued the workflow** (it did not call
#: AskUserQuestion again). That last point was the only unverified item.
#:
#: Escape hatch: setting this env to a falsy value returns to the old path. On the
#: instance, user-data injects the value through systemd `Environment=`, so editing
#: that file requires replacing the instance -- instead, create a gitignored
#: `backend/.env`; `aipds-update` does not revert it (it is an untracked file), so it
#: can be turned off without a redeploy.
FILE_QUESTIONS_ENV = "AIPDS_FILE_QUESTIONS"

#: Values that read a boolean env as **off**. The other side of the same discipline as
#: `_TRUTHY` in cli_settings.py and routes/proto_public.py -- a setting that defaults to
#: on must read "no value" as on, so it needs a list of off values, not on values.
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
    `claude --session-id=ws-m -p hi` prints "Error: Invalid session ID. Must
    be a valid UUID." and exits 1. That kills the subprocess during connect(),
    which run() can only report as a generic "agent turn failed" -- i.e. every
    turn of the workshop dies opaquely.

    And Discovery's session id is NOT a UUID today: app.py:299-304 sets
    `session_id = project_id`, which is free-form user input (routes/
    projects.py's CreateProject does not validate it), so a project named
    "ws-m" is a 100% failure. proto/session.py:124-138 already solved this
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
            #
            # A per-project directory (aipds/agent_home.py) -- the CLI writes transcripts
            # and session state here, so sharing it would let projects see each other's
            # conversations. The shared discovery-config remains only as the source of the
            # contents (CLAUDE.md), copied on every connection.
            "CLAUDE_CONFIG_DIR": driver._config_dir,
        }
        if driver._shared_config_dir:
            copy_config(driver._shared_config_dir, driver._config_dir,
                        driver._workspace)
        if driver._anthropic_model:
            env["ANTHROPIC_MODEL"] = driver._anthropic_model
        # When auto-compaction kicks in. Unset means the key is absent and the CLI
        # default applies. This is the switch that delays Discovery writing documents
        # from a summarized context in later stages (measured 264k->53k in the cli_settings header).
        env.update(cli_context_env())
        # Launch wrapper (aipds/launcher.py). When on, the CLI runs under a different uid,
        # in a sandbox that sees only this project's workspace and agent home.
        cli_path = None
        if driver._launch is not None:
            cli_path, launch_env = driver._launch.claude()
            env.update(launch_env)
        session_id, resume = _sdk_session_id(session)
        options = ClaudeAgentOptions(
            cli_path=cli_path,
            # Token-level streaming. With the default False only completed AssistantMessages
            # arrive, so a paragraph pops out all at once, and the user reads the silence
            # before it as "nothing is happening" (measured: 1,325 chars arrive as 295 deltas).
            # The completed message arrives **again, verbatim** after the deltas, so
            # `_translate` folds the duplicate -- the comment there holds that contract.
            include_partial_messages=True,
            permission_mode=driver._permission_mode,
            cwd=driver._workspace,
            env=env,
            # The effort chosen from the catalog paired with the model (`--effort`). None means
            # no flag is passed and the CLI default applies -- projects that predate this field take that path.
            effort=driver._effort,
            # Diagnostic debug log -- `--debug-file` for the bundled CLI process the SDK spawns.
            # An empty dict when the switch (AIPDS_SDK_CLI_DEBUG) is off (cli_settings).
            extra_args=cli_debug_args(driver._config_dir),
            # "user" now means OUR config dir (discovery-config/), so this is
            # safe -- and it is what lets the CLAUDE.md there be discovered.
            setting_sources=["user", "project"],
            # `skills` intentionally NOT set: unlike the prototype builder
            # (skills="all", for shadcn-design), Discovery's upstream AI-PLC
            # setup has no skills of its own -- it is CLAUDE.md plus on-demand
            # rule-file reads (discovery-config/README.md).
            # **There are no custom tools.** `mcp_servers` and `allowed_tools` are not passed
            # at all (their SDK defaults are `{}`/`[]`, so built-in tools are unaffected).
            #
            # There used to be three: `report_stage` and `handoff_prototype` moved to a
            # PostToolUse hook on 2026-08-18, and `submit_document` on 2026-08-21. The test
            # was the same every time -- **can the signal be derived from the workspace.** A tool
            # stays silent when the model does not call it, and that silence was measured three times
            # (agent/reconcile.py header + useWorkspaceStream.ts:177).
            #
            # Removing the tools gives back two things together: the inference round trip each
            # call added, and the tool descriptions loaded into the context every turn. Restoring
            # one means paying that cost, so first show that "the signal cannot be derived from the files".
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
            # Only writes flow to the mirror. load is left empty so the SDK does not create a
            # temporary config dir on resume -- resume runs from the local copy, and when there is
            # none, `_restore_transcript` restores it from S3 before connecting (aipds/transcript_restore.py header).
            session_store=(MirrorOnlyStore(driver._session_store)
                           if driver._session_store is not None else None),
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
            # PreToolUse is this product's only effective gate. The can_use_tool above is not
            # called for Write/Bash under bypassPermissions -- the SDK itself documents that
            # fact and the remedy (types.py's
            # _get_can_use_tool_shadowed_warning: "To gate every tool call, use
            # a PreToolUse hook instead").
            #
            # **Do not put AskUserQuestion in the matcher.** Per the can_use_tool description in
            # types.py, when a PreToolUse hook returns *allow*, can_use_tool is skipped too --
            # the question interception lives in that callback, so that would kill the whole
            # question round trip. For the same reason _on_pre_tool_use returns an **empty
            # dict**, not "allow", when it lets a call through.
            hooks={
                "PreToolUse": [HookMatcher(
                    matcher="Write|Edit|MultiEdit|NotebookEdit|Bash",
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
                 effort: str | None = None,
                 language: str = "ko",
                 permission_mode: str = DEFAULT_PERMISSION_MODE,
                 client_factory: Callable[[dict], Any] | None = None,
                 session_store: Any = None,
                 shared_config_dir: str | None = None,
                 launch: Any = None):
        self._workspace = workspace
        self._rules_dir = rules_dir
        self._config_dir = config_dir
        #: Source of the contents copied into config_dir on every connection (discovery-config/). None means no copy.
        self._shared_config_dir = shared_config_dir
        #: aipds.launcher.Launch, or None (= run directly as the backend uid).
        self._launch = launch
        self._s3 = s3
        # Transcript mirror. Built here rather than per-turn so the sequence
        # counter it seeds from S3 is reused across turns of one process
        # (session_store.py's header explains why that seeding exists).
        # Injectable so a test can pass None and skip S3 entirely.
        self._session_store: Any = (session_store if session_store is not None
                                    else DiscoverySessionStore(s3))
        self._anthropic_model = anthropic_model
        self._effort = effort
        # This project's output language. It flows to two places: place_rules (the language
        # directive in the workspace CLAUDE.md) and the model- and user-facing text this
        # driver produces (agent/prompts.py). Back when there were three, the third was the custom
        # tools' descriptions and return strings; that channel went away when Discovery's tools were removed on 2026-08-21.
        #
        # At first there was only place_rules, and that was the defect -- switching just the
        # directive to English left the tool descriptions and denial messages in Korean, and
        # they entered the model context every turn. The shared CLAUDE_CONFIG_DIR is shared by
        # all projects, so it still cannot hold a project language (hence its docs must be language-neutral).
        self._language = language
        self._permission_mode = _validate_permission_mode(permission_mode)
        self._client_factory = client_factory or _default_client_factory(self)
        self._client: Any = None
        #: The main agent's context usage (aipds/context_usage.py). A project has one
        #: conversation (the session id is derived from the project), so it is carried across turns.
        self._context = ContextMeter()
        # A plain list, not collections.deque -- same as builder.py, for the
        # same reason: everything here runs on one event loop, and a per-turn
        # queue is never long enough for deque's O(1) popleft to matter.
        self._queue: list[AgentEvent] = []
        self._turn_active = False
        self._turn_token: object | None = None
        self._pending_question: asyncio.Future | None = None
        self._pending_payload: str | None = None
        self._pending_iid: str | None = None
        # (tool name, detail) -- folding by name alone collapses consecutive Reads of
        # different files into one line. The INTERRUPTED_MARKER comparison uses event.text,
        # so that path is unaffected by this key becoming a tuple.
        self._last_status: tuple[str, str | None] | None = None
        # Token-delta assembly. Holds back the trailing run without whitespace so the route's
        # redaction never sees a split token (the agent/delta_buffer.py header explains why).
        self._delta_buf = WhitespaceBoundaryBuffer()
        # Whether this assistant message's text already went out as deltas. With partial
        # messages on, a completed AssistantMessage arrives again **after** the deltas
        # (measured 2026-09-04), so without this flag every paragraph renders twice. The
        # decision is made by the flag, not by the option, because paths that get no deltas
        # (the scripted fake, sessions without partial streaming) must behave as before.
        self._streamed_text = False
        # Whether the currently open block is a thinking block. content_block_stop also
        # arrives for text blocks, so without the distinction an end marker goes out even on turns with no thinking.
        self._in_thinking = False
        # Open write-tool blocks -- (parent tool_use_id, block index) -> draft.
        # index restarts at 0 for every message, and subagents' partial messages arrive on
        # the same stream, so the parent is part of the key too (agent/tool_input_draft.py).
        self._drafts: dict[tuple[str | None, int], tool_input_draft.ToolInputDraft] = {}
        # rel path -> the **set of unanswered questions already asked** from that file. A guard
        # against asking the same set twice (see _file_question_round). Kept across turns because
        # the driver instance lives as long as the project -- it is emptied on a backend restart, but
        # by then the answers are already in the file, so the "no unanswered questions" condition prevents re-asking.
        self._asked_question_sets: dict[str, tuple[str, ...]] = {}
        # rel path -> the content whose parse failure was already reported. The same note is not
        # repeated for the same content, but it is reported again if the content changes (see _on_post_tool_use).
        self._unparsed_noted: dict[str, str] = {}
        # stage name -> the status already emitted. The diff cursor for `stage` events
        # derived from `aiplc-state.md` (agent/reconcile.stage_events). The frontend accumulates
        # events, so emitting the same status again makes the sidebar list grow.
        #
        # This does not rely on the driver instance living as long as the project: if a backend
        # restart empties the cursor, the next state-file write re-emits everything once, and by
        # then the frontend has already read the state over REST and built the screen.
        self._stage_status: dict[str, str] = {}
        # Prototype ids that already emitted `prototype_ready`. Announcing twice puts two
        # cards in the chat (agent/reconcile.prototype_events).
        self._handed_off: set[str] = set()
        # artifact path -> (version, content hash). The diff cursor for `document` events
        # (agent/reconcile.document_events). The hash answers "did it change", the ordinal answers
        # "which update is this" -- the banner's dismiss remembers the version, so the value must
        # differ on every update. When a restart empties it, the ordinal goes back to 1, and
        # reconcile.document_events records that cost.
        self._doc_versions: dict[str, tuple[int, str]] = {}
        # Whether the cursor above has been seeded once from disk state. See `_seed_document_cursor`.
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

    # `_publish` used to live here. `report_stage` wrote the state file directly without going
    # through the PostToolUse hook, so that tool had to be handed a publisher. Once the tool
    # moved to the hook (agent/reconcile.py), the state file is published by the same path as
    # every other artifact, so the exception disappeared, and so did the publisher that existed
    # for it. Publishing is done by `_on_post_tool_use` calling `publish_file` directly.

    def _emit(self, event: AgentEvent) -> None:
        """Put an event on the turn queue.

        This was the `emit` sink handed to the custom tools. After those tools moved to
        hooks (the last was `submit_document` on 2026-08-21) it has no production caller,
        but it is kept because it states intent better than appending to `_queue` directly --
        derived events are appended by `_emit_stage_events`, `_emit_document_events` and
        `_handoff_stop` each on its own.
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

    async def _restore_transcript(self, session: dict) -> None:
        """Before connecting, restore the transcript from the S3 mirror if there is no local one.

        `_resolve_resume` chooses `--resume`/`--session-id` from the local file, so this must
        run before it. If the instance was replaced or the agent home is new, this is where the
        conversation carries on -- previously it silently started a new session in that case.
        A failure falls back to a new session.
        """
        if self._client is not None or self._session_store is None:
            return
        session_id, _ = _sdk_session_id(session)
        if _transcript_exists(self._config_dir, self._workspace, session_id):
            return
        try:
            await restore_transcript(self._session_store, self._config_dir,
                                     self._workspace, session_id)
        except Exception:
            _log.exception("transcript restore failed for session %s — "
                           "starting a fresh session", session_id)

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
        """Cut off the turn in progress, keeping the work done so far.

        Follows proto/builder.py's interrupt as the pattern, with one difference --
        Discovery's pending question is also mirrored to S3 (agent/pending_store.py).
        Clearing only the in-memory copy would make `GET /pending` restore a question
        nobody can answer, and the answers the user submits would resolve a future no one is listening on.

        The order is load-bearing: clean up our own state first and touch the client
        last. Even if client.interrupt() raises, no pending state is left behind.

        Idempotent: does nothing when no turn is running. The route calls this method
        based only on whether a session exists, so requests for an already finished turn arrive as a normal matter.

        Decides by `_turn_active`/`_pending_question`, not by `_turn_token`.
        For a turn parked on a question, run()'s generator ends with questions -> done
        and `_release_turn` has already run, setting `_turn_token` to None, yet the CLI
        subprocess and the `_pending_question` future are still alive (see the
        "single-turn slot" section at the top of the file) -- deciding by `_turn_token`
        would misjudge exactly the state that must be interrupted (a parked question) as "nothing to interrupt".

        **`has_live_turn` is the third case of that family (2026-08-19).** A turn that is
        still generating after its consumer went away has `_turn_active` False and no
        parked question -- judged by the two conditions above alone it was "nothing to
        interrupt", and nothing happened. Now that `run()` refuses to overwrite a turn in
        progress, that is a **deadlock**: the user can reconnect and watch it but cannot stop
        it, and new messages are refused. For a user coming back from sleep, this path is the only way out.
        """
        if self._client is None:
            return
        if (not self._turn_active and self._pending_question is None
                and not self.has_live_turn()):
            return
        if self._pending_question is not None and not self._pending_question.done():
            # The _on_can_use_tool that was awaiting this future is abandoned along with the turn.
            self._pending_question.cancel()
        self._clear_pending_state()
        await self._clear_pending_quietly()
        # A questions event left in the queue is a card nobody can answer -- letting it
        # through would put a form on screen.
        self._queue = [e for e in self._queue if e.kind != "questions"]
        # Record that the turn was interrupted. It goes out as the existing status rather
        # than a new kind in order to reuse an event shape the frontend already handles --
        # on seeing this marker, the frontend draws one "중단됨" ("Interrupted") line for that turn in
        # the UI language. The marker lives only in the live SSE queue -- it does not enter
        # the transcript, so it is not restored after a refresh.
        self._queue.append(AgentEvent(kind="status", text=INTERRUPTED_MARKER))
        await self._client.interrupt()

    async def _on_pre_tool_use(self, input_data, tool_use_id, context) -> dict:
        """Discovery's write-scope gate. The rulings live in agent/discovery_guard.py.

        Why a hook: Discovery runs under bypassPermissions, so Write/Bash are
        auto-approved and never reach can_use_tool -- the only gate the SDK designates is
        PreToolUse (the rationale is in the hooks comment of the factory above and the discovery_guard header).

        **Letting a call through is an empty dict.** Returning "allow" skips can_use_tool too,
        killing the AskUserQuestion interception (the can_use_tool description in types.py).
        """
        name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input") or {}
        if name in _FILE_TOOLS or name == NOTEBOOK_TOOL:
            path = tool_write_path(name, tool_input)
            offender = write_denial(path, self._workspace)
            reason = (None if offender is None
                      else prompts.write_outside_docs(self._language, offender))
            if offender is None:
                offender = web_owned_denial(path, self._workspace)
                reason = (None if offender is None
                          else prompts.web_owned_doc(self._language, offender))
            if offender is None:
                offender = audit_overwrite_denial(name, path, self._workspace)
                reason = (None if offender is None
                          else prompts.audit_overwrite(self._language, offender))
        elif name == "Bash":
            offender = bash_denial(tool_input.get("command"))
            reason = (None if offender is None
                      else prompts.build_command_refused(self._language, offender))
            if offender is None:
                offender = settings_denial(tool_input.get("command"))
                reason = (None if offender is None
                          else prompts.cli_settings_refused(self._language, offender))
        else:
            # The matcher only catches the tools above, but if the hook config and this
            # branch ever diverge, the call must pass silently -- blocking an unknown tool stalls the turn.
            return {}
        if reason is None:
            return {}
        # Log it: the denial reason goes only to the model, so the operator needs a
        # separate way to see what was blocked.
        _log.warning("discovery gate denied %s: %s", name, offender)

        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}

    def _file_question_round(self, rel: str) -> tuple[Any, str] | None:
        # Three possible returns: None (not applicable) / ("unparsed", raw text) / (QuestionFile, raw text).
        # A parse failure is kept distinct from None because silence here means the question is lost.
        """If the just-written `rel` is a **question round to ask**, return `(parse result, raw text)`.

        Why the raw text comes along: the caller must upload that file to S3 (it must be in
        the source of truth before the card is advertised), and there is no reason to read the same file twice.

        All four conditions must hold:

        1. **`looks_like_question_file` must be true** -- there is an `[Answer]:` slot at
           the start of a line, and the name is not in `NEVER_QUESTION_FILES`.
           Inclusion is decided by content, not by name: upstream has broken its own naming
           convention (`{phase}-questions.md`) before -- it put questions in
           `design-context.md` too (question_file_answers.py header). Conversely, files whose
           **purpose upstream defines**, such as audit.md, are excluded by name.

           Until 2026-08-18 this gate diverged from the answer write-back (here it was
           a plain `"[Answer]:" in md` substring check, there a `^` anchor). So when audit.md
           recorded the tag, only this side matched, and the agent said "audit.md is not a
           question file, so I will remove that notation" and **damaged its own audit record** --
           the very record `core-workflow.md:303` requires to be kept verbatim.
        2. `parse_ok`. If it does not parse, **pass silently.** The upstream format is not
           stable (2026-08-17: 1 of 8 files escaped the parser), so blocking here would keep
           the questions from appearing on screen at all -- it must degrade, not block.
        3. At least one question must have an empty answer. The agent re-reading the answers
           and rewriting the file is normal behavior, and it must not be asked again then.
        4. **Never ask the same set of unanswered questions twice.** The guard is keyed on the
           set of unanswered questions rather than on the file because questions added after
           the answers (`### Clarification Question 2`) must be asked.

        Reads from disk -- the tool_input of `Edit`/`MultiEdit` does not hold the full content,
        and PostToolUse runs after the write has finished.
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
            # Do not skip silently -- now that AskUserQuestion is denied, this would be the
            # complete loss of the questions (the rationale behind prompts.file_questions_unparsed).
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
        """Read `aiplc-state.md` and emit `stage` only for stages whose status changed.

        The only place the cursor (`self._stage_status`) is updated -- the hook path and the
        turn-boundary reconciliation go through the **same function**, so both see the same
        diff. With two copies, one path would re-emit what the other already emitted.
        """
        events, self._stage_status = reconcile.stage_events(
            reconcile.read_state(Path(self._workspace)), self._stage_status)
        for ev in events:
            self._queue.append(ev)

    def _emit_document_events(self) -> None:
        """Emit `document` only for artifacts whose content changed. Replaces the old `submit_document`.

        One function for the same reason as `_emit_stage_events` -- the hook path and the turn
        boundary must go through the **same cursor**. With two copies, the turn boundary would
        re-announce documents the hook already announced, and the update banner would appear twice per document.
        """
        events, self._doc_versions = reconcile.document_events(
            Path(self._workspace), self._doc_versions)
        for ev in events:
            self._queue.append(ev)

    def _seed_document_cursor(self) -> None:
        """When the first turn starts, mark the documents that **already exist** as "seen".

        `document` is derived from a workspace scan, so a turn that starts with an empty cursor
        sees every existing document as new. The cursor is held by this driver, so that state
        arises on **a new driver's first turn** -- the first attach after a backend restart or
        redeploy, when the tree is already populated (a cold restore downloaded everything, or
        the local workspace is left over from an earlier turn). A project with ten documents would
        get ten announcements, and the update banner would point at the last document in scan order, not the one just written.

        **Done only once.** Seeding every turn would silently swallow documents changed between
        turns (writes from another instance, restored content that differs). After the first
        turn, every change is announced.

        **Why here rather than in `__init__`** is that same restore: when the driver is
        created the workspace is empty, and it gets populated just before the runner calls
        `run()`. Seeding at construction would see nothing, and then the first turn would
        announce everything again.
        """
        if self._docs_seeded:
            return
        self._docs_seeded = True
        _, self._doc_versions = reconcile.document_events(
            Path(self._workspace), self._doc_versions)

    def _reconcile_turn(self) -> None:
        """Bring the UI in line with the workspace before the turn ends. Called by `_pump`.

        Recovers here the changes the hook did not see (via Bash) and the events it missed (batch
        drops). A handoff is only announced and **does not cut off the turn** -- the turn is
        already ending, and there is no place here to return `continue_: False` anyway.

        Swallows exceptions. Reconciliation is a backstop, so if it failed the turn it would
        become a new cause of failure rather than a backstop (runner._sync_abandoned_turn makes the same call).
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

        `None` means it was not confirmed, and the turn continues. It is not confirmed when
        no spec exists yet -- the Prototypes tab builds its cards from specs
        (`routes/prototypes.py` builds the list with `layout.discover`), so build instructions
        alone would show the user an empty tab. That is why the old `handoff_prototype` checked
        that a spec existed, and that check moved to `reconcile.handed_off`.

        **The turn ends for the same reason as with question files.** Discovery's work ends
        here and the next action belongs to the user (build from the Prototypes tab). Without
        ending it, the agent carries on to upstream Step 4 (Iterate) or asks for credentials --
        both are measured failures. So `stopReason` **specifies** the next action: the principle
        in the prompts.py header is that giving only a reason makes the model improvise.
        """
        events, cursor = reconcile.prototype_events(
            Path(self._workspace), self._handed_off)
        if not events:
            # A handoff already announced passes silently (the build instructions were rewritten) --
            # no second card appears, and the turn is not cut off again.
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
        # **Publish before advertising.** A UI that receives `file_changed` immediately comes
        # to read that document (WorkspaceDocPanel), and every read path goes to the canonical
        # store (S3). Deferring canonical publishing to turn end yields "it says it was written but
        # it is not in the list / selecting it shows no content / it shows briefly and disappears" --
        # exactly what happened on 2026-08-18, when all 16 measured S3 timestamps fell within 1 second of turn end.
        #
        # A failure does not kill the turn (publish_file swallows it). The batch sync at turn
        # end is still the backstop.
        await publish_file(
            self._s3,
            Path(self._workspace),
            rel,
            on_published=self._on_file_published,
        )
        # A question file is an artifact too -- the document panel refreshes on this event, so
        # it is emitted first, regardless of the branches below.
        self._queue.append(AgentEvent(kind="file_changed", path=rel))
        # Stage badges: derived by **parsing** the state file (replaces the old `report_stage`
        # tool -- the agent/reconcile.py header has the full story). The upstream rules require
        # the agent to update this file itself (common/workflow-changes.md,
        # discovery/prototype-validation.md Step 10), so the signal is already in the workspace.
        #
        # Reads **disk**, not the hook payload. Edit carries only a patch, so the payload cannot
        # give the full file, and `_file_question_round` made the same choice for the same reason.
        if rel == reconcile.STATE_KEY:
            self._emit_stage_events()
        # The document update banner and the document panel's activeDoc: **derived** from
        # artifact writes (replaces the old `submit_document` tool -- though instructed to call it
        # for every document, it was mostly not called. The frontend recorded the measurement:
        # useWorkspaceStream.ts:177 "most documents are created with file_write alone,
        # without submit_document"). It was the same kind of silence as stages and handoffs.
        if reconcile.is_document(rel):
            self._emit_document_events()
        # Prototype handoff: the moment the last artifact of Step 3 is written. Unlike the old
        # `handoff_prototype` tool, the model cannot forget it -- zero tab prompts in
        # ws-a-v5 on 2026-08-17 were why that tool was created, and the tool merely pushed the
        # same failure, "silent unless called", one step later.
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
            # Do not repeat the same note for the same content (prevents an endless round trip
            # within the turn). Report again when the content **changes** -- that is the case of a
            # rewrite that is still wrong, and staying silent then makes the questions disappear again.
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
        # `interrupt_id` is an empty string: there is no parked can_use_tool future, so the
        # answers return to the **file**, not to the turn. The frontend tells the difference by
        # `file` and sends the answers to POST /projects/{pid}/questions/{name}/answers.
        self._queue.append(AgentEvent(kind="questions", payload=json.dumps(
            {"interrupt_id": "", "file": rel, "questions": qfile},
            ensure_ascii=False, default=lambda o: o.model_dump())))
        # **Upload the file to S3 first -- do not wait for the turn-end sync.**
        #
        # Failure measured on 2026-08-17: in a real turn the card appeared, but `GET /pending`
        # returned `file=None` and the answer submission returned 404. The hook reads the local
        # file, but that file reaches S3 (the source of truth) only at the runner's done/error
        # sync. The gap is a window, and in that window `pending()` cannot find the file the
        # marker points to, and the answer submission 404s because `runner.read_file` reads S3.
        #
        # By the time the card is advertised, the file must already be in the source of truth.
        # The content is already in hand, so uploading here is the cheapest and surest option
        # (one put per round). The runner's sync uploading the same content again later is harmless.
        try:
            # The file was already published above (publish_file) -- every artifact goes
            # through the same path, so no question-file-specific upload is kept. The marker
            # comes **after** it: reversing the order would leave a window where the marker points to a file not in the source of truth.
            await save_pending_file(self._s3, file=rel)
        except Exception:
            # The live card is already in the queue -- even on failure this turn's questions
            # appear on screen; only refresh restore and answer submission fail. Killing the turn would be worse.
            _log.exception("publishing the open question file failed")
        # The hook does not wait for a person -- it just stops the turn immediately. Measured
        # (2026-08-17): `continue_: False` ends with `terminal_reason='hook_stopped'` +
        # `is_error=False`, so `_translate` already treats it as a normal `done`, and the tool
        # calls batched after it in the same message are not executed (that is intended -- the
        # model cannot recreate the same questions through AskUserQuestion).
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
        # With the file path on, this tool is not used. It is a **denial**, not a removal --
        # removing just the interception would make the questions vanish silently the moment
        # the model calls the tool (neither on screen nor in the chat). A denial also supplies the alternative action.
        #
        # Why there is one switch: with both paths alive at once, the agent writes the file
        # (the hook shows the card) and then calls this tool too, so the same questions appear twice.
        if _file_questions_enabled():
            _log.info("AskUserQuestion denied — file questions are the only path")
            return PermissionResultDeny(
                message=prompts.ask_user_question_denied(self._language))
        import uuid
        # Unfold a payload that arrived as a string into a list here -- passed through without
        # normalization, question_file_from_sdk walks the string character by character and
        # blows up with AttributeError, and that exception leaks out of this callback and kills
        # the turn. The 3 observed rejections were blocked by the CLI **before** this callback,
        # so they are not rescued here (the rationale is in normalize_sdk_questions's docstring).
        sdk_questions = normalize_sdk_questions(input_data.get("questions"))
        # The restore join key. The SDK gives this callback a tool_use_id (non-empty is
        # guaranteed by the wire protocol), and the transcript's tool_result carries the same id,
        # so keying the answer record by it lets restore join exactly, without guessing from
        # order or timestamps (see the agent/answer_store.py header). It is read with getattr
        # because test doubles that call this callback directly may pass an abbreviated context.
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
        # The record for restore. Written **before returning updated_input** -- this return
        # resumes the turn and the next tool soon starts running, so if it came after, a failure
        # would make it hard to pinpoint, even from the logs, which round's record is missing.
        await self._save_answers_quietly(tool_use_id, iid, qfile, answers)
        # Also plant the answers in the question file's `[Answer]:` slots, because the ai-plc
        # workflow reads those slots -- the rationale and the reason for text matching are
        # in the agent/question_file_answers.py header. It happens here (just before returning)
        # for the same reason as _save_answers_quietly: this return resumes the turn and the
        # next tool soon starts, so if it came after, the file could land later than the next
        # stage's read. record_answers swallows any failure and returns an empty list.
        for rel in record_answers(self._workspace, sdk_questions, answers):
            await self._mirror_question_file_quietly(rel)
            self._queue.append(AgentEvent(kind="file_changed", path=rel))
        return PermissionResultAllow(updated_input={
            "questions": sdk_questions,
            "answers": sdk_answers,
        })

    async def _mirror_question_file_quietly(self, rel: str) -> None:
        """Upload a question file that got answers written back to S3 as well. Never kills the turn.

        **Why it is needed.** record_answers writes to the local workspace file, but
        `runner.read_file` reads from S3 (runner.py:55) -- and that is what the on-screen
        artifact panel and the next stage see. Writing only locally leaves the answers
        **invisible** until the turn ends and `_sync_workspace_to_s3` runs.

        And that delay is also a loss window: `_restore_workspace_from_s3` runs at the start
        of every turn, and its comment says "S3 always wins" (runner.py:79). If a turn is
        abandoned without a terminal event (`_sync_abandoned_turn` is best-effort), the next
        turn overwrites the local copy with S3's empty file and the answers disappear.

        Why the local file is re-read for upload: it uploads exactly the bytes on disk.
        Carrying a separate in-memory copy would let the two drift apart.
        """
        try:
            content = (Path(self._workspace) / rel).read_text(encoding="utf-8")
            await self._s3.put(rel, content)
        except Exception:
            # The local file is already updated and the turn-end sync gives a second chance --
            # killing the turn here would lose the answers just submitted.
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
        """Record the submitted answers in S3. Never fails the turn.

        The same judgment as the pending mirror -- this record is a restore convenience, and
        an S3 hiccup killing the turn the user just answered would be worse. Without the record,
        restore falls back to the same path as old sessions (the CLI's prose wording).

        Skipped when there is no tool_use_id: that value is the restore join key, so a record
        written without it cannot be tied to a round and is useless to the reader.
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

    def _translate_stream_event(self, ev: dict,
                                parent: str | None = None) -> list[AgentEvent]:
        """Partial-message frame -> events. `event` is the raw Claude API streaming event.

        **Only text deltas become body text.** `thinking_delta` is not put in the body buffer --
        on this path its content is empty (measured 0 chars), but the day it carries a value,
        the model's reasoning leaking into the answer would be a bug. `signature_delta` is neither body nor trace.

        **Write-tool input becomes a draft.** When a Write/Edit block opens, `tool_input_draft`
        unpacks its input fragments and emits them as `draft` events -- so the screen is not blank
        while the full file is being generated (measured in that module's header).

        **Unknown frames are dropped silently.** message_start, message_delta and message_stop
        carry nothing to show, and a new frame type must not break anything here.
        """
        events: list[AgentEvent] = []
        etype = ev.get("type")
        key = (parent, ev.get("index", 0))
        if etype == "content_block_start":
            block = ev.get("content_block") or {}
            if block.get("type") == "thinking":
                self._in_thinking = True
                events.append(AgentEvent(kind="status", text=THINKING_MARKER))
            elif block.get("type") == "tool_use" and block.get("name") in _FILE_TOOLS:
                draft = tool_input_draft.ToolInputDraft(
                    tool_use_id=block.get("id") or "", tool=block["name"],
                    to_rel=lambda fp: _rel(fp, self._workspace))
                self._drafts[key] = draft
                events.extend(draft.start(time.monotonic()))
        elif etype == "content_block_delta":
            delta = ev.get("delta") or {}
            if delta.get("type") == "input_json_delta":
                draft = self._drafts.get(key)
                if draft is not None:
                    events.extend(draft.feed(delta.get("partial_json") or "",
                                             time.monotonic()))
            elif delta.get("type") == "text_delta":
                self._streamed_text = True
                safe = self._delta_buf.feed(delta.get("text") or "")
                if safe:
                    events.append(AgentEvent(kind="message", text=safe))
        elif etype == "content_block_stop":
            draft = self._drafts.pop(key, None)
            if draft is not None:
                events.extend(draft.finish(time.monotonic()))
            tail = self._delta_buf.flush()
            if tail:
                events.append(AgentEvent(kind="message", text=tail))
            if self._in_thinking:
                self._in_thinking = False
                events.append(AgentEvent(kind="status",
                                         text=THINKING_DONE_MARKER))
        return events

    def _translate(self, msg, reader: "_MessageReader | None" = None) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        tname = type(msg).__name__
        # `MirrorErrorMessage` is a **subclass** of `SystemMessage`, and the parser
        # always builds that subclass for mirror_error
        # (`_internal/message_parser.py`). So the previous condition, which checked only
        # `tname == "SystemMessage"`, **was never once true** -- the warning this comment
        # calls "the only signal" was never actually logged. This is where code that branches
        # on names dies on a subclass, and the same trap recurred with the prototype build's
        # `Task*` messages (aipds/agent_activity.py header).
        #
        # Accept both names: if upstream reverts this class, it does not silently die again.
        if (tname in ("SystemMessage", "MirrorErrorMessage")
                and getattr(msg, "subtype", "") == "mirror_error"):
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
        if tname == "StreamEvent":
            return self._translate_stream_event(
                getattr(msg, "event", None) or {},
                parent=getattr(msg, "parent_tool_use_id", None))
        if tname == "AssistantMessage":
            # If this message's text already went out as deltas, the TextBlock is a duplicate.
            # The flag is consumed **per message** -- leaving it set would silently drop the
            # next answer that arrives without deltas.
            streamed, self._streamed_text = self._streamed_text, False
            if streamed:
                # If the message ended without a content_block_stop being seen, the tail is
                # stuck in the buffer. TextBlocks are skipped below, so unless it is emitted
                # here the end of the sentence is lost.
                tail = self._delta_buf.flush()
                if tail:
                    events.append(AgentEvent(kind="message", text=tail))
            for block in getattr(msg, "content", []):
                btype = type(block).__name__
                if btype == "TextBlock":
                    if streamed:
                        continue
                    events.append(AgentEvent(kind="message", text=block.text))
                elif btype == "ToolUseBlock":
                    # Send what it did as well -- with just `Read` shown, the trace loses
                    # its point (tool_trace module header). Only the value is sent; the icon
                    # and separator of `🔍 Read · …` are drawn by the frontend in the UI language.
                    detail = tool_detail(block.name, getattr(block, "input", None))
                    # Put detail in the duplicate-folding key. Folding by name alone collapses
                    # three consecutive Reads into one `Read` line -- even for different files.
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
                # showed "중단됨" (Task 5's status line, correct) stacked with
                # "이번 턴이 실패했습니다" (a lie) -- the real-CLI probe that
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
                # An interrupt is not logged as an error: it is the normal result of a
                # button the user just pressed, not a failure we need to hunt down. Logging
                # it as an error would pollute the workshop log on every interrupt and add
                # noise when looking for real failures (429/500/529, a wedged tool).
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
        # Whether it is time to measure context usage. Inside translation this is **only
        # flagged** -- translation must be synchronous (docstring below), and the query is an await.
        sample_due = False
        def translate_into_outbox() -> None:
            """Move messages inbox -> outbox, translating. Never yields.

            Wholly synchronous, so a message is never in neither place: it
            leaves `inbox` and enters `outbox` with no suspension in between.
            `outbox` lives on the reader, so anything still there when this
            generator dies belongs to the next pump over the same reader.
            """
            nonlocal ended, sample_due
            while reader.inbox and not ended:
                msg = reader.inbox.pop(0)
                sample_due = sample_due or wants_sample(msg)
                for ev in self._translate(msg, reader):
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
            if sample_due:
                # Measure **after** the emission -- the first query takes ~650ms (measured in
                # aipds/context_usage.py), so doing it first would delay the first answer by
                # that much. Nothing is lost if this await is cancelled: no event exists until
                # the query returns. The result is held by the outbox, and the next emission or
                # the terminal harvest below delivers it before `done` (the ownership rule as is).
                sample_due = False
                usage = await self._context.sample(self._client)
                if usage is not None:
                    reader.outbox.append(usage)
            if reader.ended and not reader.inbox:
                ended = True
            if asked or ended:
                break
        if reader.error is not None:
            # Surfaced on the caller's stack so `_stream` can degrade it to the
            # contract's "agent turn failed" -- messages already relayed above
            # have reached the consumer first.
            raise reader.error
        # Turn-boundary reconciliation -- recover from the workspace what the hooks missed.
        #
        # **Why the hooks alone are not enough.** PostToolUse is attached only to
        # `Write|Edit|MultiEdit`, so when the agent edits a file through Bash (`python3 -c`,
        # `sed`, redirection) the hook does not see it. The discovery_guard.py header already
        # records the same limit -- Bash is arbitrary code execution, so no denylist can cover
        # every path, and adding Bash to the matcher still would not reveal the target file from the command.
        #
        # So neither the moment-by-moment declaration (tools) nor the per-write observation
        # (hooks) is the last word -- **consistency at the boundary** is. Reading the disk once
        # here covers Bash workarounds, lost hooks and batch drops alike. The lost `report_stage`
        # of 2026-08-18 test123456 is caught here too.
        #
        # **Its position before the terminal emission is the point.** `frontend/lib/api/sse.ts:29`
        # closes the EventSource on `done`, so events after it never reach the screen (invariant 1
        # above). Queued here, the drain loop below emits them ahead of the terminal event.
        #
        # What was already emitted is not emitted again -- the two cursors (`_stage_status`,
        # `_handed_off`) are shared with the hook path, so reconciliation usually does nothing. That is normal.
        #
        # It also runs on `asked` (a turn that ended in a question). That path is exactly where
        # the loss was observed: a question-file write cut off the turn and the calls batched with it vanished.
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
    # Why that matters: when the consumer is cancelled (the turn job's task —
    # project delete, backend shutdown), runner.py clears its own slot
    # synchronously in the same `finally`. A turn that starts on the very next
    # tick must not bounce off ours with "turn already in progress" for a turn
    # that no longer exists.

    def _acquire_turn(self) -> object | None:
        """Claim the turn slot; None if one is already running. The token
        identifies THIS turn so a rejected caller cannot release the slot the
        live turn is holding.
        """
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
        turn_started = time.perf_counter()
        first_sdk_event_logged = False
        first_text_logged = False
        reader_for_turn: _MessageReader | None = None
        self._last_status = None
        # Delta state lives as long as the instance (the driver keeps serving one project),
        # while a turn can be abandoned midway (SSE disconnect, proxy timeout, page
        # navigation -- runner.py:144-152). Without resetting it, `_streamed_text` stays True,
        # **the next turn's first answer is judged a duplicate and disappears entirely**, and
        # the tail stuck in the buffer comes out in its place.
        self._delta_buf.flush()
        self._streamed_text = False
        self._in_thinking = False
        self._current_session_id = session.get("session_id")
        try:
            # `resume` is only what the CALLER wants; `_resolve_resume` decides
            # from the CLI's transcript, because both flags are fatal when they
            # disagree with the disk. Building the dict here (rather than
            # inside _ensure_client) keeps the client factory's contract
            # unchanged: it still receives a session dict with `resume`
            # already settled.
            await self._restore_transcript(session)
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

        Writing them every turn also helps with language: even if the language directive
        does not survive in the workspace, it is laid down again on the next turn."""
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
        # Mark the documents already in the workspace at this point as "seen" -- so a new
        # driver's first turn does not announce every existing document as an update.
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
            # **Do not let a new turn overwrite an abandoned one.** A turn's consumer is the
            # turn job (aipds/turn_job.py), which does not go away, but if that job is cancelled
            # (project deletion, backend shutdown) the CLI turn keeps running and the reader keeps
            # reading. A new turn that got here and entered `_stream` would then have
            # `_retire_reader()` **cancel the in-progress turn's reader** and stack a `query()` onto the same CLI session.
            #
            # It matters that this sits **after** the parked-question short-circuit: that reader
            # is alive too (has_live_turn is true), so putting this first would turn the path
            # that re-shows the question form into this refusal.
            if self.has_live_turn():
                _log.info("refusing a new turn: one is still streaming with no "
                          "consumer")
                yield AgentEvent(kind="error", text="turn already in progress")
                return
            async for ev in self._stream(text, session):
                yield ev
        finally:
            self._release_turn(token)

    def has_live_turn(self) -> bool:
        """A turn is still streaming with nobody consuming it.

        **Why `_turn_active` cannot tell.** That flag says whether there is a *consumer*.
        When the consumer (the turn job) is cancelled, `run()`'s finally clears it, but the
        CLI turn itself keeps running and `_MessageReader` keeps reading. Telling those two
        apart is what this function does.

        Why `ended` is checked too: a parked question's reader is alive but has not ended
        (hence True), while a normally finished turn's reader has a finished task or is `ended`.
        """
        reader = self._reader
        return (reader is not None and not reader.task.done()
                and not reader.ended)

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
        # Mark the documents already in the workspace at this point as "seen" -- so a new
        # driver's first turn does not announce every existing document as an update.
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

        Third, it checks the **file question round**. That round leaves no parked future
        (the PostToolUse hook ended the turn), so neither path above has anything.
        Why it comes last: a value on either path above means there is a live future
        waiting for an answer, and then the turn resumes only once that question is answered.
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

        **Reads S3, not the local workspace.** The local copy is restored only at turn start
        (runner.py's `_restore_workspace_from_s3`), so a `GET /pending` right after a restart
        would see an empty directory. S3 is the single source of truth.

        None when there are no unanswered questions -- that is this round's end signal, and
        it is why the answer-submission path does not have to delete anything.
        """
        rel = await load_pending_file(self._s3)
        if rel is None:
            return None
        from aipds.parsers.questions import parse_question_file
        try:
            md = await self._s3.get(rel)
        except FileNotFoundError:
            # The file is gone (deleted or moved). Restore is a convenience, so give up quietly.
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
