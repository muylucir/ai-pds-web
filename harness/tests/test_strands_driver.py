import asyncio
import json
from collections import deque

import pytest
from strands_driver import StrandsDriver, _questions_event_from_interrupts


class FakeInterrupt:
    def __init__(self, id="i-1", reason=None):
        self.id = id
        self.name = "ask_questions"
        self.reason = reason or {"questions_payload": {"name": "q", "questions": []}}


class FakeResult:
    def __init__(self, stop_reason="end_turn", interrupts=None):
        self.stop_reason = stop_reason
        self.interrupts = interrupts


class FakeInterruptState:
    def __init__(self, activated=False, interrupts=None):
        self.activated = activated
        self.interrupts = interrupts or {}


class FakeAgent:
    """Duck-typed strands Agent: stream_async yields scripted event dicts.
    The last event carries {"result": FakeResult}."""
    def __init__(self, script, interrupt_state=None):
        self._script = script
        self.calls = []
        self._interrupt_state = interrupt_state

    async def stream_async(self, prompt):
        self.calls.append(prompt)
        for ev in self._script:
            yield ev


def make_driver(script, emitted_during_tools=()):
    def factory(session, emit):
        for ev in emitted_during_tools:
            pass  # tools emit via `emit` at runtime; tests emit inline via script
        return FakeAgent(script)
    return StrandsDriver(workspace="/workspace", agent_factory=factory)


async def collect(aiter):
    return [e async for e in aiter]


SESSION = {"session_id": "p1", "bucket": "", "region": "ap-northeast-1", "prefix": "sessions"}


@pytest.mark.asyncio
async def test_text_deltas_become_message_events_and_done():
    drv = make_driver([{"data": "안녕"}, {"data": "하세요"},
                       {"result": FakeResult("end_turn")}])
    evs = await collect(drv.run("hi", SESSION))
    assert [(e.kind, e.text) for e in evs[:2]] == [("message", "안녕"), ("message", "하세요")]
    assert evs[-1].kind == "done"

@pytest.mark.asyncio
async def test_interrupt_result_yields_questions_then_done():
    payload = {"name": "pain-point-questions", "questions": []}
    drv = make_driver([{"data": "질문 준비"},
                       {"result": FakeResult("interrupt", [FakeInterrupt("i-9", {"questions_payload": payload})])}])
    evs = await collect(drv.run("시작", SESSION))
    q = next(e for e in evs if e.kind == "questions")
    body = json.loads(q.payload)
    assert body["interrupt_id"] == "i-9"
    assert body["questions"] == payload
    assert evs[-1].kind == "done"

@pytest.mark.asyncio
async def test_run_answers_resumes_with_interrupt_response():
    drv = make_driver([{"data": "반영"}, {"result": FakeResult("end_turn")}])
    evs = await collect(drv.run_answers("i-9", {"1": "A"}, SESSION))
    agent = drv._agents[SESSION["session_id"]]
    resume_prompt = agent.calls[0]
    assert resume_prompt == [{"interruptResponse": {"interruptId": "i-9", "response": {"1": "A"}}}]
    assert evs[-1].kind == "done"

@pytest.mark.asyncio
async def test_agent_cached_per_session_id():
    drv = make_driver([{"result": FakeResult("end_turn")}])
    await collect(drv.run("a", SESSION))
    first = drv._agents["p1"]
    await collect(drv.run("b", SESSION))
    assert drv._agents["p1"] is first

@pytest.mark.asyncio
async def test_stream_error_yields_error_event():
    class Boom(FakeAgent):
        async def stream_async(self, prompt):
            yield {"data": "x"}
            raise RuntimeError("bedrock down")
    drv = StrandsDriver(workspace="/workspace", agent_factory=lambda s, e: Boom([]))
    evs = await collect(drv.run("hi", SESSION))
    assert evs[-1].kind == "error"
    assert "bedrock down" not in (evs[-1].text or "")  # no raw internals to the user


@pytest.mark.asyncio
async def test_agent_construction_failure_yields_only_generic_error_event():
    """Agent construction (BedrockModel init, missing env var, missing rules
    file, bad S3SessionManager args, etc.) can fail before any stream_async
    call happens at all. That must not escape run()/run_answers() as a raw
    exception — it must degrade to the same sanitized error contract as an
    in-stream failure."""
    def boom_factory(session, emit):
        raise KeyError("ANTHROPIC_MODEL")
    drv = StrandsDriver(workspace="/workspace", agent_factory=boom_factory)
    evs = await collect(drv.run("hi", SESSION))
    assert [e.kind for e in evs] == ["error"]
    assert "ANTHROPIC_MODEL" not in (evs[-1].text or "")  # no raw internals to the user

@pytest.mark.asyncio
async def test_pending_returns_none_on_construction_failure():
    """pending() probes agent state after a restore; if that probe itself
    blows up (same construction failure modes as run()), it must report
    "no pending questions visible" (None) rather than raise/500."""
    def boom_factory(session, emit):
        raise RuntimeError("s3 session manager unreachable")
    drv = StrandsDriver(workspace="/workspace", agent_factory=boom_factory)
    assert await drv.pending(SESSION) is None


@pytest.mark.asyncio
async def test_free_text_while_interrupt_pending_reminds_without_calling_model():
    """B1: a plain-string prompt sent while the agent's _interrupt_state is
    activated must NOT hit strands' _InterruptState.resume (which raises
    TypeError for a non-interruptResponse prompt) -- the driver must
    short-circuit before ever calling stream_async."""
    payload = {"name": "pain-point-questions", "questions": []}
    interrupt = FakeInterrupt("i-9", {"questions_payload": payload})
    state = FakeInterruptState(activated=True, interrupts={"i-9": interrupt})
    agent = FakeAgent([{"result": FakeResult("end_turn")}], interrupt_state=state)

    def factory(session, emit):
        return agent
    drv = StrandsDriver(workspace="/workspace", agent_factory=factory)

    evs = await collect(drv.run("hi", SESSION))

    assert agent.calls == []  # model never invoked
    kinds = [e.kind for e in evs]
    assert kinds == ["message", "questions", "done"]
    q = next(e for e in evs if e.kind == "questions")
    assert json.loads(q.payload)["interrupt_id"] == "i-9"


@pytest.mark.asyncio
async def test_run_answers_proceeds_normally_even_with_activated_interrupt_state():
    """The B1 guard only applies to plain-string prompts; run_answers's list
    prompt must stream through to the model as before."""
    state = FakeInterruptState(activated=True, interrupts={})
    agent = FakeAgent([{"data": "반영"}, {"result": FakeResult("end_turn")}],
                      interrupt_state=state)

    def factory(session, emit):
        return agent
    drv = StrandsDriver(workspace="/workspace", agent_factory=factory)

    evs = await collect(drv.run_answers("i-9", {"1": "A"}, SESSION))

    assert agent.calls == [[{"interruptResponse": {"interruptId": "i-9", "response": {"1": "A"}}}]]
    assert evs[-1].kind == "done"


@pytest.mark.asyncio
async def test_status_events_deduped_on_repeated_current_tool_use():
    """B2: the SDK emits current_tool_use once per ContentBlockDelta, so a
    large tool input yields many identical frames -- only the NAME CHANGES
    should produce a status event."""
    drv = make_driver([
        {"current_tool_use": {"name": "ask_questions"}},
        {"current_tool_use": {"name": "ask_questions"}},
        {"current_tool_use": {"name": "ask_questions"}},
        {"current_tool_use": {"name": "file_write"}},
        {"result": FakeResult("end_turn")},
    ])
    evs = await collect(drv.run("hi", SESSION))
    status_events = [e for e in evs if e.kind == "status"]
    assert [e.text for e in status_events] == ["ask_questions", "file_write"]


@pytest.mark.asyncio
async def test_emit_from_worker_thread_lands_in_deque():
    """Integration smoke test (Task 2 review recommendation): drive a REAL
    Task-2 tool (report_stage) through asyncio.to_thread — exactly how strands
    dispatches plain @tool functions (strands/tools/decorator.py:638) — with
    emit=deque.append as the sink, and confirm the event is observable from
    the event-loop thread afterward. This is the actual concern the brief's
    correction addresses: a deque's append/popleft are atomic w.r.t. the GIL,
    so cross-thread emission is safe where asyncio.Queue.put_nowait would not
    be."""
    from aiplc_tools import build_tools

    d: deque = deque()
    tools = build_tools("/workspace", d.append)
    report_stage = next(t for t in tools if getattr(t, "tool_name", None) == "report_stage")

    await asyncio.to_thread(report_stage, stage="Envision", status="in_progress", summary="s")

    assert len(d) == 1
    ev = d.popleft()
    assert ev.kind == "stage"
    assert json.loads(ev.payload)["stage"] == "Envision"
