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
#   resolves that future and drains the REST of the same turn through a FRESH
#   `receive_response()` iterator over the same client (never a second
#   `query()`). See `_continue_after_answers` for why a fresh iterator is safe.
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
from pathlib import PurePosixPath
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

    And Discovery's session id is NOT a UUID today: app.py:255 sets
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

    # ---- plumbing ----

    def _emit(self, event: AgentEvent) -> None:
        """The `emit` sink handed to build_tools -- report_stage/
        submit_document push stage/document events through here."""
        self._queue.append(event)

    def drain_queue(self) -> list[AgentEvent]:
        out = []
        while self._queue:
            out.append(self._queue.pop(0))
        return out

    async def _ensure_client(self, session: dict):
        if self._client is None:
            _suppress_shadowed_callback_warning()
            self._client = self._client_factory(session)
            await self._client.connect()
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

    async def _pump(self, agen) -> AsyncIterator[AgentEvent]:
        """Drain one turn's SDK messages, racing them against the callback queue.

        Race the next message against the hook/tool-callback queue: while an
        AskUserQuestion is pending, receive_response() yields nothing at all,
        so a plain `async for` would never let a queued `questions` event reach
        the SSE stream. Poll the queue on a short timeout instead of blocking
        indefinitely on the next message.

        Where this DIVERGES from builder.py's loop: a queued `questions` event
        ends the turn here (`questions` then `done`, then return), because
        Discovery answers arrive on a separate stream -- see the module
        docstring. The suspended can_use_tool callback is left parked on its
        future for `run_answers` to resolve.

        That divergence is also why every exit path must first consume any SDK
        message that has ALREADY been delivered. `asyncio.wait` reports
        `done=∅` on timeout, but `next_msg` can resolve during that very tick,
        and returning without reading it DESTROYS the message: anyio's
        `send_nowait` hands an item straight to a parked receiver instead of
        buffering it (anyio/streams/memory.py:210-217), so cancelling that
        receiver in the `finally` below drops the item -- it is not left in the
        buffer for the fresh iterator `_continue_after_answers` opens.
        Measured: the assistant message vanished at gap=0, survived at
        gap>=0.001.

        This is the COMMON case, not a rare race. The CLI writes the
        `{"type":"assistant"}` message (the model's "why I'm asking" prose plus
        the AskUserQuestion tool_use) and the `control_request` back to back in
        one read-loop pass (claude_agent_sdk/_internal/query.py:250-322) with
        no model latency in between -- and driver.py's _CONTACT_ADDENDUM:44-45
        *requires* the model to explain itself before asking. Losing it means a
        bare question card with no explanation, every single time. builder.py
        never hit this because it never returns on `asked`.
        """
        next_msg: asyncio.Future = asyncio.ensure_future(agen.__anext__())
        ended = False

        async def ready_events():
            """Translate every message already delivered, re-arming the
            receive each time. Runs on every loop pass BEFORE the queue is
            drained, so an already-delivered message is never discarded by the
            cancel in the `finally` (see docstring)."""
            nonlocal next_msg, ended
            while next_msg.done():
                try:
                    msg = next_msg.result()
                except StopAsyncIteration:
                    ended = True
                    return
                for ev in self._translate(msg):
                    yield ev
                next_msg = asyncio.ensure_future(agen.__anext__())

        try:
            while not ended:
                await asyncio.wait({next_msg}, timeout=_POLL_SECONDS)
                async for ev in ready_events():
                    yield ev
                asked = False
                for ev in self.drain_queue():
                    yield ev
                    asked = asked or ev.kind == "questions"
                if asked and not ended:
                    # ready_events() above already drained everything delivered
                    # before the callback queued this question, which is the
                    # ordering that matters: the CLI cannot send more messages
                    # until we answer the permission request, so nothing new
                    # can arrive between there and here.
                    yield AgentEvent(kind="done")
                    return
        finally:
            # Two jobs. (1) The consumer may abandon this generator mid-stream
            # (SSE client disconnect -> aclose() -> GeneratorExit), and the
            # `asked` return above always leaves one receive in flight: without
            # this cancel the __anext__ future outlives the generator and
            # asyncio logs "Task was destroyed but it is pending!". (2) The
            # cancel DISCARDS whatever that receive was handed, which is why
            # ready_events() must run on every exit path first.
            if not next_msg.done():
                next_msg.cancel()
        for ev in self.drain_queue():
            yield ev

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
            connect_session = dict(session)
            connect_session["resume"] = resume
            client = await self._ensure_client(connect_session)
            await client.query(text)
            async for ev in self._pump(client.receive_response().__aiter__()):
                yield ev
        except Exception:
            _log.exception("claude sdk turn failed")
            for ev in self.drain_queue():
                yield ev
            yield AgentEvent(kind="error", text="agent turn failed")
            return

    async def _continue_after_answers(self) -> AsyncIterator[AgentEvent]:
        """Drain the REST of a turn that `_pump` parked on a question.

        No `query()` here -- the turn is still mid-flight; the CLI was only
        waiting for the permission response that resolving the future
        produces. A FRESH `receive_response()` iterator is what picks the turn
        back up: it reads the same buffered anyio message stream the abandoned
        iterator was reading (claude_agent_sdk/_internal/query.py owns that
        stream, not the generator), so no message is lost, and it terminates
        on this turn's ResultMessage the way the original would have.

        Assumes the caller already holds the turn slot.
        """
        if self._client is None:  # defensive: no client, nothing to resume
            yield AgentEvent(kind="error", text="agent turn failed")
            return
        try:
            async for ev in self._pump(self._client.receive_response().__aiter__()):
                yield ev
        except Exception:
            _log.exception("claude sdk answer turn failed")
            for ev in self.drain_queue():
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
        """
        if self._pending_question is not None and not self._pending_question.done():
            self._pending_question.cancel()
        self._clear_pending_state()
        client, self._client = self._client, None
        if client is None:
            return
        try:
            await client.disconnect()
        except Exception:
            _log.exception("claude driver disconnect failed")
