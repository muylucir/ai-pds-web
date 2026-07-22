import pytest
from pathfinder.agent.driver import StrandsDriver, _questions_event_from_interrupts


class FakeResult:
    def __init__(self, stop_reason="end_turn", interrupts=None):
        self.stop_reason = stop_reason
        self.interrupts = interrupts


class FakeInterrupt:
    def __init__(self, id="i-1", reason=None):
        self.id = id
        self.name = "ask_questions"
        self.reason = reason or {"questions_payload": {"name": "q", "questions": []}}


class FakeInterruptState:
    def __init__(self, activated=False, interrupts=None):
        self.activated = activated
        self.interrupts = interrupts or {}


class FakeAgent:
    def __init__(self, script, interrupt_state=None):
        self._script = script
        self.calls = []
        self._interrupt_state = interrupt_state

    async def stream_async(self, prompt):
        self.calls.append(prompt)
        for ev in self._script:
            yield ev


def make_driver(script, interrupt_state=None):
    def factory(session, emit):
        return FakeAgent(script, interrupt_state)
    return StrandsDriver(workspace="/tmp/ws", rules_dir="/tmp/rules",
                         agent_factory=factory)


SESSION = {"session_id": "p1", "bucket": "", "region": "ap-northeast-2", "prefix": "sessions"}


async def _collect(aiter):
    return [e async for e in aiter]


async def test_text_deltas_become_message_events_and_done():
    drv = make_driver([{"data": "안녕"}, {"data": "하세요"},
                       {"result": FakeResult("end_turn")}])
    evs = await _collect(drv.run("hi", SESSION))
    assert [e.kind for e in evs] == ["message", "message", "done"]
    assert evs[0].text == "안녕"


async def test_interrupt_result_yields_questions_then_done():
    itr = FakeInterrupt()
    drv = make_driver([{"result": FakeResult("interrupt", interrupts=[itr])}])
    evs = await _collect(drv.run("go", SESSION))
    assert [e.kind for e in evs] == ["questions", "done"]


async def test_run_answers_resumes_with_interrupt_response():
    captured = {}
    def factory(session, emit):
        agent = FakeAgent([{"result": FakeResult("end_turn")}])
        orig = agent.stream_async
        async def spy(prompt):
            captured["prompt"] = prompt
            async for ev in orig(prompt):
                yield ev
        agent.stream_async = spy
        return agent
    drv = StrandsDriver(workspace="/tmp/ws", rules_dir="/tmp/rules", agent_factory=factory)
    await _collect(drv.run_answers("i-7", {"1": "A"}, SESSION))
    assert captured["prompt"] == [{"interruptResponse": {"interruptId": "i-7", "response": {"1": "A"}}}]


async def test_stream_error_yields_error_event():
    class Boom(FakeAgent):
        async def stream_async(self, prompt):
            raise RuntimeError("kaboom")
            yield  # unreachable
    def factory(session, emit):
        return Boom([])
    drv = StrandsDriver(workspace="/tmp/ws", rules_dir="/tmp/rules", agent_factory=factory)
    evs = await _collect(drv.run("x", SESSION))
    assert evs[-1].kind == "error"
    assert "agent turn failed" in evs[-1].text


async def test_agent_construction_failure_yields_generic_error():
    def factory(session, emit):
        raise RuntimeError("bedrock init failed")
    drv = StrandsDriver(workspace="/tmp/ws", rules_dir="/tmp/rules", agent_factory=factory)
    evs = await _collect(drv.run("x", SESSION))
    assert [e.kind for e in evs] == ["error"]
    assert "agent turn failed" in evs[0].text


async def test_pending_returns_none_on_construction_failure():
    def factory(session, emit):
        raise RuntimeError("boom")
    drv = StrandsDriver(workspace="/tmp/ws", rules_dir="/tmp/rules", agent_factory=factory)
    assert await drv.pending(SESSION) is None


async def test_free_text_while_interrupt_pending_reminds_without_calling_model():
    state = FakeInterruptState(activated=True, interrupts={"i-1": FakeInterrupt()})
    drv = make_driver([{"data": "MODEL WAS CALLED"}], interrupt_state=state)
    evs = await _collect(drv.run("아무 말", SESSION))
    kinds = [e.kind for e in evs]
    assert "message" in kinds and kinds[-1] == "done"
    assert all(e.text != "MODEL WAS CALLED" for e in evs)  # 모델 호출 안 함


async def test_status_events_deduped_on_repeated_current_tool_use():
    drv = make_driver([
        {"current_tool_use": {"name": "file_write"}},
        {"current_tool_use": {"name": "file_write"}},
        {"current_tool_use": {"name": "file_read"}},
        {"result": FakeResult("end_turn")},
    ])
    evs = await _collect(drv.run("go", SESSION))
    status = [e.text for e in evs if e.kind == "status"]
    assert status == ["file_write", "file_read"]


async def test_agent_cached_per_session_id():
    drv = make_driver([{"result": FakeResult("end_turn")}])
    await _collect(drv.run("a", SESSION))
    first = drv._agents["p1"]
    await _collect(drv.run("b", SESSION))
    assert drv._agents["p1"] is first


async def test_run_answers_proceeds_normally_even_with_activated_interrupt_state():
    state = FakeInterruptState(activated=True, interrupts={})
    agent = FakeAgent([{"data": "반영"}, {"result": FakeResult("end_turn")}],
                      interrupt_state=state)
    def factory(session, emit):
        return agent
    drv = StrandsDriver(workspace="/tmp/ws", rules_dir="/tmp/rules", agent_factory=factory)
    evs = await _collect(drv.run_answers("i-9", {"1": "A"}, SESSION))
    assert agent.calls == [[{"interruptResponse": {"interruptId": "i-9", "response": {"1": "A"}}}]]
    assert evs[-1].kind == "done"


async def test_emit_from_worker_thread_lands_in_deque(tmp_path):
    import asyncio
    from collections import deque
    from pathfinder.agent.tools import build_tools
    d = deque()
    tools = build_tools(str(tmp_path / "ws"), str(tmp_path / "rules"), d.append)
    report_stage = next(t for t in tools if getattr(t, "tool_name", None) == "report_stage")
    await asyncio.to_thread(report_stage, stage="Envision", status="in_progress", summary="s")
    # report_stage now also upserts aiplc-state.md and emits a second
    # file_changed event for it (Task 1: state_sync) — both must land in
    # the deque via the same emit callback from the worker thread.
    assert len(d) == 2
    ev = d.popleft()
    assert ev.kind == "stage"
    ev2 = d.popleft()
    assert ev2.kind == "file_changed"
    assert ev2.path == "aiplc-docs/aiplc-state.md"
