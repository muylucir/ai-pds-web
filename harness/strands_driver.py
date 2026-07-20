# harness/strands_driver.py — Strands agent loop INSIDE the MicroVM.
# Replaces claude_driver's subprocess+stream-json with an in-process agent.
# Conversation context persists to S3 via S3SessionManager (spec §2): the VM
# can die and a new one resumes the same session_id, pending interrupt included.
from __future__ import annotations
import collections
import json
import logging
import os
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from events import AgentEvent
from aiplc_tools import build_tools, QUESTIONS_SCHEMA_HINT

_log = logging.getLogger("harness.strands")

_RULES_DIR = "aiplc-rules/aws-aiplc-rules"
_COMMON_DIR = "aiplc-rules/aws-aiplc-rule-details/common"

_CONTACT_ADDENDUM = f"""
## Pathfinder 통합 규약 (UI 접점 — 반드시 준수)
- 사용자에게 객관식 질문을 할 때는 반드시 ask_questions 도구를 사용한다.
  질문 파일(aiplc-docs/**-questions.md)은 기록용으로 계속 작성하되, 질문
  전달 자체는 도구로만 한다. {QUESTIONS_SCHEMA_HINT}
- 스테이지를 시작/완료할 때마다 report_stage 도구를 호출한다.
- discovery-document를 생성/갱신할 때마다 submit_document 도구를 호출한다.
- 파일 접근은 file_read / file_write / file_append 도구만 사용한다 (경로는 워크스페이스 상대).
- file_write는 파일 **전체를 덮어쓴다**. audit.md에 엔트리를 추가할 때는 반드시
  file_append를 사용한다 — 새 엔트리만 담아 file_write를 호출하면 기존 감사
  기록이 전부 유실된다.
"""


def _system_prompt(workspace: str) -> str:
    """core-workflow + common rules verbatim (rules stay data — spec §1),
    then the integration addendum. Stage-detail rules are NOT inlined; the
    core workflow instructs the agent to file_read them on demand."""
    ws = Path(workspace)
    parts = [(ws / _RULES_DIR / "core-workflow.md").read_text(encoding="utf-8")]
    common = ws / _COMMON_DIR
    if common.is_dir():
        for f in sorted(common.glob("*.md")):
            parts.append(f"\n\n---\n# RULE DETAIL: common/{f.name}\n" + f.read_text(encoding="utf-8"))
    parts.append(_CONTACT_ADDENDUM)
    return "".join(parts)


def _session_manager(session: dict):
    if session.get("bucket"):
        from strands.session import S3SessionManager
        return S3SessionManager(
            session_id=session["session_id"], bucket=session["bucket"],
            prefix=session.get("prefix", "sessions"),
            region_name=session.get("region") or None)
    # Local/test fallback: file sessions under the workspace (survives within
    # the VM only — fine for tests and the local drill).
    from strands.session import FileSessionManager
    return FileSessionManager(session_id=session["session_id"],
                              storage_dir="/workspace/.sessions")


def _default_agent_factory(workspace: str):
    def factory(session: dict, emit: Callable[[AgentEvent], None]):
        from strands import Agent
        from strands.models import BedrockModel
        model = BedrockModel(model_id=os.environ["ANTHROPIC_MODEL"])
        return Agent(
            model=model,
            system_prompt=_system_prompt(workspace),
            tools=build_tools(workspace, emit),
            session_manager=_session_manager(session),
            callback_handler=None,   # we consume stream_async, not callbacks
        )
    return factory


def _questions_event_from_interrupts(interrupts) -> AgentEvent | None:
    for itr in interrupts or []:
        reason = getattr(itr, "reason", None) or {}
        if "questions_payload" in reason:
            return AgentEvent(kind="questions", payload=json.dumps(
                {"interrupt_id": itr.id, "questions": reason["questions_payload"]},
                ensure_ascii=False))
    return None


class StrandsDriver:
    def __init__(self, workspace: str,
                 agent_factory: Callable[[dict, Callable], Any] | None = None):
        self._workspace = workspace
        self._factory = agent_factory or _default_agent_factory(workspace)
        self._agents: dict[str, Any] = {}
        self._queues: dict[str, collections.deque] = {}

    def _agent_for(self, session: dict):
        sid = session["session_id"]
        if sid not in self._agents:
            queue: collections.deque = collections.deque()
            # strands dispatches plain @tool functions via asyncio.to_thread
            # (strands/tools/decorator.py:638), so `emit` runs on a WORKER
            # THREAD while we drain on the event-loop thread. deque.append /
            # popleft are each atomic under the GIL, so this cross-thread
            # handoff is safe without extra locking — asyncio.Queue's
            # put_nowait is NOT documented thread-safe and must not be used
            # here.
            self._agents[sid] = self._factory(session, queue.append)
            self._queues[sid] = queue
        return self._agents[sid], self._queues[sid]

    async def _stream(self, prompt, session: dict) -> AsyncIterator[AgentEvent]:
        # Agent construction (BedrockModel init, _system_prompt's rules-file
        # reads, S3SessionManager setup, etc.) can fail before there's any
        # `queue` to drain — guard it in its own try/except so a raw
        # exception (missing env var, missing rules file, bad bucket/region)
        # never escapes `run`/`run_answers` as anything other than the
        # sanitized error contract every other failure path already honors.
        try:
            agent, queue = self._agent_for(session)
        except Exception:
            _log.exception("strands agent construction failed")
            yield AgentEvent(kind="error", text="agent turn failed")
            return
        # B1: strands' _InterruptState.resume raises TypeError when resumed
        # with anything other than an interruptResponse prompt. A plain-text
        # message sent while a question is still pending would otherwise hit
        # that TypeError and surface as an opaque "agent turn failed" -- so
        # short-circuit here: never call the model, just re-remind the user
        # of the pending question(s). run_answers (list prompt) is unaffected.
        if isinstance(prompt, str):
            state = getattr(agent, "_interrupt_state", None)
            if state is not None and getattr(state, "activated", False):
                yield AgentEvent(
                    kind="message",
                    text="진행 중인 질문에 먼저 답변해 주세요 — 우측 패널의 질문 폼을 이용하세요.",
                )
                q_ev = _questions_event_from_interrupts(list(state.interrupts.values()))
                if q_ev is not None:
                    yield q_ev
                yield AgentEvent(kind="done")
                return
        result = None
        last_status: str | None = None
        try:
            async for ev in agent.stream_async(prompt):
                # Drain tool-emitted structured events first (stage/document/
                # file_changed land here mid-stream, in tool-execution order).
                while queue:
                    yield queue.popleft()
                if "data" in ev:
                    yield AgentEvent(kind="message", text=ev["data"])
                elif "current_tool_use" in ev:
                    # B2: the SDK emits current_tool_use once per
                    # ContentBlockDelta, so a large tool input yields dozens
                    # of identical frames -- dedupe on the tool name so the
                    # UI only sees a status change, not spam.
                    name = (ev["current_tool_use"] or {}).get("name")
                    if name and name != last_status:
                        last_status = name
                        yield AgentEvent(kind="status", text=name)
                if "result" in ev:
                    result = ev["result"]
        except Exception:
            _log.exception("strands turn failed")
            while queue:
                yield queue.popleft()
            yield AgentEvent(kind="error", text="agent turn failed")
            return
        while queue:
            yield queue.popleft()
        if result is not None and getattr(result, "stop_reason", None) == "interrupt":
            q_ev = _questions_event_from_interrupts(result.interrupts)
            if q_ev is not None:
                yield q_ev
        yield AgentEvent(kind="done")

    def run(self, text: str, session: dict) -> AsyncIterator[AgentEvent]:
        return self._stream(text, session)

    def run_answers(self, interrupt_id: str, answers: dict[str, str],
                    session: dict) -> AsyncIterator[AgentEvent]:
        prompt = [{"interruptResponse": {"interruptId": interrupt_id,
                                         "response": answers}}]
        return self._stream(prompt, session)

    async def pending(self, session: dict) -> str | None:
        """Pending interrupt after restore. No public accessor exists in the
        SDK (verified v1.48); _interrupt_state is the documented-in-source
        session-persisted field.

        A failed probe (e.g. agent construction blows up the same way it can
        in `_stream`) must not 500 the caller with internals — log server-side
        and report "no pending questions visible" (None) rather than raise."""
        try:
            agent, _ = self._agent_for(session)
            state = getattr(agent, "_interrupt_state", None)
            if state is None or not getattr(state, "activated", False):
                return None
            ev = _questions_event_from_interrupts(list(state.interrupts.values()))
            return ev.payload if ev else None
        except Exception:
            _log.exception("strands pending-interrupt probe failed")
            return None
