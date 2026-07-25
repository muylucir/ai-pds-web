# backend/pathfinder/proto/builder.py — the prototype build agent, running
# IN-PROCESS in the backend (was harness/sdk_driver.py inside a Tokyo MicroVM).
#
# One build session = one connected ClaudeSDKClient. Hook/tool callbacks run on
# the SDK's tasks while run() drains on the caller's loop -- both on the SAME
# event loop, so a plain list handoff is safe.
#
# Three things differ from the VM-era driver:
#   1. CLAUDE_CONFIG_DIR is always injected. The bundled binary is ordinary
#      Claude Code and reads ~/.claude when this is unset -- harmless in the
#      VM (empty home) but on the workshop EC2 that is the operator's personal
#      skills/agents/CLAUDE.md, which would leak into every workshop build and
#      make results depend on host config.
#   2. session_store + resume make the transcript durable, so a session can be
#      resumed days later or after a backend redeploy.
#   3. disconnect() exists. Stopping the VM used to reclaim the process; now
#      the idle timer must do it explicitly.
from __future__ import annotations

import asyncio
import logging
from pathlib import PurePosixPath
from typing import Any, AsyncIterator, Callable

from pathfinder.models import AgentEvent

_log = logging.getLogger(__name__)

_FILE_TOOLS = {"Write", "Edit", "MultiEdit"}
_LETTERS = "ABCDEFGHIJ"


def _to_question_file(sdk_questions: list[dict]) -> dict:
    """SDK AskUserQuestion input → frontend QuestionFile shape (types.ts),
    so QuestionForm renders it unmodified. Letters index the SDK options."""
    questions = []
    for i, q in enumerate(sdk_questions, start=1):
        options = [{"letter": _LETTERS[j],
                    "text": f"{o.get('label', '')} — {o.get('description', '')}".rstrip(" —"),
                    "is_other": False, "recommended": False}
                   for j, o in enumerate(q.get("options", []))]
        questions.append({
            "number": i, "category": q.get("header") or None,
            "text": q.get("question", ""), "options": options,
            "answer": None, "multi_select": bool(q.get("multiSelect")),
        })
    return {"name": "prototype-questions", "preamble": None,
            "questions": questions, "parse_ok": True, "raw_markdown": None}


def _rel(path: str, workspace: str) -> str | None:
    """Make a tool's file_path workspace-relative; reject escapes.
    (Ported from the old claude_driver._rel — see its docstring for why any
    `..` in the relativized parts is an escape, not merely relative.)

    Fix vs. the brief's literal version: `relative_to` also raises ValueError
    when `path` is absolute but shares no prefix with `workspace` at all
    (e.g. "/etc/passwd" vs workspace "/workspace") — not just for genuinely
    relative inputs. The original fallback (`path.lstrip("/")`) treated both
    cases as "already relative", which let an unrelated absolute path escape
    undetected (caught by test_post_tool_hook_rejects_escape). Only fall back
    to the lstrip path when `path` was not absolute to begin with; an
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


def _default_client_factory(builder: "PrototypeBuilder") -> Callable[[], Any]:
    def make():
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
        from claude_agent_sdk.types import HookMatcher

        env = {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            # Swap the config HOME rather than disabling settings entirely
            # (setting_sources=[]): this keeps a place to put OUR skills and
            # subagents later, and keeps the local transcript copy under a
            # Pathfinder-owned path instead of the operator's home.
            "CLAUDE_CONFIG_DIR": builder._config_dir,
        }
        if builder._anthropic_model:
            env["ANTHROPIC_MODEL"] = builder._anthropic_model
        options = ClaudeAgentOptions(
            permission_mode="bypassPermissions",
            cwd=builder._workspace,
            env=env,
            # "user" now means OUR config dir, so this is safe -- and it is
            # what `skills=[...]` needs open when we eventually enable one.
            setting_sources=["user", "project"],
            session_id=builder._session_id,
            resume=builder._session_id if builder._resume else None,
            session_store=builder._session_store,
            can_use_tool=builder._on_can_use_tool,
            hooks={"PostToolUse": [HookMatcher(matcher="Write|Edit|MultiEdit",
                                               hooks=[builder._on_post_tool_use])]},
        )
        return ClaudeSDKClient(options=options)
    return make


class PrototypeBuilder:
    def __init__(self, workspace: str, config_dir: str, session_id: str,
                 resume: bool, session_store: Any = None,
                 anthropic_model: str | None = None,
                 client_factory: Callable[[], Any] | None = None):
        self._workspace = workspace
        self._config_dir = config_dir
        self._session_id = session_id
        self._resume = resume
        self._session_store = session_store
        self._anthropic_model = anthropic_model
        self._factory = client_factory or _default_client_factory(self)
        self._client: Any = None
        # A plain list, not collections.deque: tests assert `_queue == []`
        # after draining, and deque never compares equal to a list literal.
        self._queue: list[AgentEvent] = []
        self._turn_active = False
        self._interrupted = False
        self._pending_question: asyncio.Future | None = None
        self._pending_payload: str | None = None
        self._pending_iid: str | None = None

    def drain_queue(self) -> list[AgentEvent]:
        out = []
        while self._queue:
            out.append(self._queue.pop(0))
        return out

    async def _ensure_client(self):
        if self._client is None:
            self._client = self._factory()
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

    async def _on_can_use_tool(self, tool_name, input_data, context):
        from claude_agent_sdk.types import PermissionResultAllow
        if tool_name != "AskUserQuestion":
            return PermissionResultAllow(updated_input=input_data)
        import json as _json, uuid
        iid = uuid.uuid4().hex
        sdk_questions = input_data.get("questions", [])
        qfile = _to_question_file(sdk_questions)
        payload = _json.dumps({"interrupt_id": iid, "questions": qfile},
                              ensure_ascii=False)
        self._pending_payload = payload
        self._pending_iid = iid
        loop = asyncio.get_running_loop()
        self._pending_question = loop.create_future()
        self._queue.append(AgentEvent(kind="questions", payload=payload))
        try:
            answers = await self._pending_question  # stays open until /answers
        except asyncio.CancelledError:
            # interrupt() cancels this future and clears pending state
            # itself, but guard defensively in case cancellation reached
            # us some other way (e.g. task cancellation from outside).
            self._pending_payload = None
            self._pending_question = None
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
            self._pending_payload = None
            self._pending_question = None
        return PermissionResultAllow(updated_input={
            "questions": sdk_questions,
            "answers": sdk_answers,
        })

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

    async def run(self, text: str) -> AsyncIterator[AgentEvent]:
        if self._turn_active:
            yield AgentEvent(kind="error", text="turn already in progress")
            return
        self._turn_active = True
        self._interrupted = False
        self._last_status: str | None = None
        next_msg: asyncio.Future | None = None
        try:
            client = await self._ensure_client()
            await client.query(text)
            # Race the next message against the hook/tool-callback queue:
            # while an AskUserQuestion is pending, receive_response() yields
            # nothing at all, so a plain `async for` would never let a
            # queued `questions` event reach the SSE stream. Poll the queue
            # on a short timeout instead of blocking indefinitely on the
            # next message.
            agen = client.receive_response().__aiter__()
            next_msg = asyncio.ensure_future(agen.__anext__())
            while True:
                assert next_msg is not None  # loop invariant (narrows Optional)
                done, _ = await asyncio.wait({next_msg}, timeout=0.05)
                for ev in self.drain_queue():
                    yield ev
                if not done:
                    continue
                try:
                    msg = next_msg.result()
                except StopAsyncIteration:
                    break
                for ev in self._translate(msg):
                    if ev.kind == "done" and self._interrupted:
                        yield AgentEvent(kind="status", text="interrupted")
                    yield ev
                next_msg = asyncio.ensure_future(agen.__anext__())
        except asyncio.CancelledError:
            # interrupt() cancels the pending-question future; that
            # cancellation surfaces here via next_msg.result(). It is OUR
            # deliberate interrupt, not the consumer cancelling us -- so the
            # stream must still end with a proper terminal event (the UI
            # otherwise shows a dead connection for a user-initiated stop).
            # A genuine external cancellation (consumer task cancelled) has
            # _interrupted unset and must propagate untouched.
            if not self._interrupted:
                raise
            for ev in self.drain_queue():
                yield ev
            yield AgentEvent(kind="status", text="interrupted")
            yield AgentEvent(kind="done")
            return
        except Exception:
            _log.exception("sdk turn failed")
            for ev in self.drain_queue():
                yield ev
            yield AgentEvent(kind="error", text="agent turn failed")
            return
        finally:
            self._turn_active = False
            # The consumer may abandon this generator mid-stream (SSE client
            # disconnect -> aclose() -> GeneratorExit): without this cancel
            # the in-flight __anext__ future outlives the generator and
            # asyncio logs "Task was destroyed but it is pending!".
            if next_msg is not None and not next_msg.done():
                next_msg.cancel()
        for ev in self.drain_queue():
            yield ev

    async def interrupt(self) -> None:
        if self._client is None or not self._turn_active:
            return  # idempotent no-op
        self._interrupted = True
        # A pending question cannot survive an interrupt: _on_can_use_tool's
        # await is abandoned along with the rest of this turn, so leaving
        # _pending_payload set would make pending() report a question that
        # can never be answered, and a later submit_answers() would resolve
        # a future nobody is listening on anymore (returns True but nothing
        # continues). Clear it before touching the client, so our state is
        # consistent even if client.interrupt() raises.
        if self._pending_question is not None and not self._pending_question.done():
            self._pending_question.cancel()
        self._pending_payload = None
        self._pending_question = None
        self._pending_iid = None
        await self._client.interrupt()

    async def submit_answers(self, interrupt_id: str,
                             answers: dict[str, str]) -> bool:
        if (self._pending_question is None
                or getattr(self, "_pending_iid", None) != interrupt_id
                or self._pending_question.done()):
            return False
        self._pending_question.set_result(answers)
        return True

    async def pending(self) -> str | None:
        return self._pending_payload

    async def disconnect(self) -> None:
        """Tear down the claude subprocess. Idempotent -- close() and the idle
        timer can both reach here."""
        client, self._client = self._client, None
        if client is None:
            return
        try:
            await client.disconnect()
        except Exception:
            _log.exception("builder disconnect failed")
