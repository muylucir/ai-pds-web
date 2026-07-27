# 계약을 동작하는 기존 드라이버로 확정한다. 여기가 통과해야 driver_contract.py가
# 신뢰할 수 있는 스펙이 되고, ClaudeDriver가 같은 함수를 통과하면 동등하다.
import pytest

from pathfinder.agent.driver import StrandsDriver
from tests.driver_contract import assert_driver_contract


class _FakeInterrupt:
    """strands의 인터럽트 오브젝트를 흉내낸다 — driver.py의
    _questions_event_from_interrupts가 읽는 필드는 .id와 .reason(dict)뿐이다."""

    def __init__(self, id: str, reason: dict):
        self.id = id
        self.reason = reason


class _FakeResult:
    """strands Agent.stream_async가 마지막에 내는 {"result": ...} 값을
    흉내낸다. driver.py가 읽는 필드는 stop_reason과 interrupts뿐이다."""

    def __init__(self, stop_reason: str = "end_turn", interrupts=None):
        self.stop_reason = stop_reason
        self.interrupts = interrupts or []


class _FakeStrandsAgent:
    """strands Agent.stream_async를 흉내낸다 — 실제 SDK 이벤트 dict 형태
    ({"data":...} / {"current_tool_use":...} / {"result":...}).

    질문(ask_questions)은 emit 콜백이 아니라 tool_context.interrupt(...)를
    통해 전달된다(tools.py:73) — 그 결과가 stream_async의 마지막
    {"result": ...} 프레임에 stop_reason="interrupt" + interrupts로 실린다.
    driver.py의 _stream이 그 result에서만 kind=questions를 만들어낸다
    (emit 콜백은 stage/document/file_changed에만 쓰인다 — tools.py 참고).
    이 흉내가 emit으로 questions를 직접 밀어넣으면 driver.py의 실제 질문
    변환 경로(_questions_event_from_interrupts)를 전혀 거치지 않게 되어
    계약 테스트가 무의미해진다."""

    def __init__(self, scripted: dict, emit):
        self._scripted = scripted
        self._emit = emit
        self._interrupt_state = None

    async def stream_async(self, prompt):
        if self._scripted.get("raise"):
            raise RuntimeError("boom")
        for text in self._scripted.get("text", []):
            yield {"data": text}
        for name in self._scripted.get("tools", []):
            yield {"current_tool_use": {"name": name}}
        if self._scripted.get("questions"):
            itr = _FakeInterrupt("i-strands", {
                "questions_payload": {"name": "q", "questions": []},
            })
            yield {"result": _FakeResult("interrupt", [itr])}
        else:
            yield {"result": _FakeResult("end_turn")}


def _make_strands_driver(scripted: dict):
    def factory(session, emit):
        return _FakeStrandsAgent(scripted, emit)
    driver = StrandsDriver(workspace="/tmp/ws", rules_dir="/tmp/rules",
                          agent_factory=factory)
    return driver, {"session_id": "s-1"}


@pytest.mark.asyncio
async def test_strands_driver_satisfies_the_contract():
    await assert_driver_contract(_make_strands_driver)
