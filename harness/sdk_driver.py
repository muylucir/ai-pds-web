# harness/sdk_driver.py  (runs INSIDE the MicroVM)
from __future__ import annotations
import asyncio
import logging
from pathlib import PurePosixPath
from typing import Any, AsyncIterator, Callable

from events import AgentEvent

_log = logging.getLogger("harness.sdk_driver")

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


def _default_client_factory(workspace: str, driver: "SdkDriver") -> Callable[[], Any]:
    def make():
        from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
        from claude_agent_sdk.types import HookMatcher
        options = ClaudeAgentOptions(
            permission_mode="bypassPermissions",
            cwd=workspace,
            env={"CLAUDE_CODE_USE_BEDROCK": "1"},
            can_use_tool=driver._on_can_use_tool,
            hooks={"PostToolUse": [HookMatcher(matcher="Write|Edit|MultiEdit",
                                               hooks=[driver._on_post_tool_use])]},
        )
        return ClaudeSDKClient(options=options)
    return make


class SdkDriver:
    """One build session = one connected ClaudeSDKClient (multi-turn context
    lives in the client; no --continue flag to manage). Hook/tool callbacks
    run on the SDK's tasks while run() drains on the caller's loop — both on
    the SAME event loop, so a plain list handoff is safe."""

    def __init__(self, workspace: str,
                 client_factory: Callable[[], Any] | None = None):
        self._workspace = workspace
        self._factory = client_factory or _default_client_factory(workspace, self)
        self._client: Any = None
        # A plain list, not collections.deque: tests assert `d._queue == []`
        # after draining, and deque only compares equal to another deque
        # (never to a list literal) — `collections.deque() == []` is False.
        # popleft()'s O(1) vs list.pop(0)'s O(n) doesn't matter at this
        # queue's per-turn size (a handful of tool-use events).
        self._queue: list[AgentEvent] = []
        self._turn_active = False
        self._interrupted = False
        # Question wait state (Task 3): set while an AskUserQuestion tool
        # call is blocked awaiting /answers.
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
        answers = await self._pending_question  # stays open until /answers
        # "number -> letter/text" (our contract) → "question text -> label"
        # (SDK contract).
        sdk_answers = {}
        for k, v in answers.items():
            try:
                q = sdk_questions[int(k) - 1]
            except (ValueError, IndexError):
                continue
            sdk_answers[q.get("question", "")] = self._answer_to_sdk(
                v, q.get("options", []))
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
        except Exception:
            _log.exception("sdk turn failed")
            for ev in self.drain_queue():
                yield ev
            yield AgentEvent(kind="error", text="agent turn failed")
            return
        finally:
            self._turn_active = False
        for ev in self.drain_queue():
            yield ev

    async def interrupt(self) -> None:
        if self._client is None or not self._turn_active:
            return  # idempotent no-op
        self._interrupted = True
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
