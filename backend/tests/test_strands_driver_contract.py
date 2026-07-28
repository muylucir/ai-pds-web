# 계약을 동작하는 기존 드라이버로 확정한다. 여기가 통과해야 driver_contract.py가
# 신뢰할 수 있는 스펙이 되고, ClaudeDriver가 같은 함수를 통과하면 동등하다.
import pytest
from strands.interrupt import _InterruptState

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
    계약 테스트가 무의미해진다.

    `_interrupt_state`도 같은 이유로 실제 SDK를 따른다. 예전엔 이 필드가 항상
    None이었고, 그래서 질문 턴 뒤 StrandsDriver.pending()이 None을 돌려줬다 —
    그게 "두 드라이버가 pending()에서 서로 다르게 동작한다"처럼 보이게 만든
    원인이었지만, 실제 원인은 이 가짜였다. 실제 SDK는 도구가 인터럽트를 올리면
    event_loop.py:806-808에서 context를 채운 뒤 `_interrupt_state.activate()`를
    부르고, StrandsDriver.pending()이 읽는 것이 정확히 그 필드다
    (driver.py:231-235). 실제 `_InterruptState`를 그대로 import해 같은 순서로
    채우면(활성화 없이는 activated=False라 여전히 None) 실제 드라이버도
    페이로드를 돌려준다 — 실측으로 확인했다. 그래야 계약이 요구하는
    "질문 뒤 pending()은 그 라운드를 돌려준다"를 양쪽 드라이버가 *정직하게*
    만족한다."""

    def __init__(self, scripted: dict, emit):
        self._scripted = scripted
        self._emit = emit
        # 실제 SDK가 Agent.__init__에서 하는 것과 같다(agent.py의
        # `self._interrupt_state = _InterruptState()`).
        self._interrupt_state = _InterruptState()

    def _raise_interrupt(self, itr) -> None:
        """도구가 인터럽트를 올렸을 때 실제 SDK가 남기는 상태를 그대로 만든다
        (event_loop.py:806-808 — context 설정 후 activate()). 순서까지 같게
        두는 이유는 pending()이 activated 플래그를 보기 때문이다."""
        self._interrupt_state.interrupts = {itr.id: itr}
        self._interrupt_state.context = {"tool_use_message": {}, "tool_results": []}
        self._interrupt_state.activate()

    async def stream_async(self, prompt):
        if self._scripted.get("raise"):
            raise RuntimeError("boom")
        for text in self._scripted.get("text", []):
            yield {"data": text}
        for name in self._scripted.get("tools", []):
            yield {"current_tool_use": {"name": name}}
        if self._scripted.get("echo_answers"):
            # StrandsDriver.run_answers (driver.py:216-220) builds exactly
            # this prompt shape from (interrupt_id, answers) — a one-element
            # list containing an "interruptResponse" dict with
            # "interruptId"/"response" keys. Reading it back here and
            # echoing it as a message is how the contract observes that
            # run_answers actually forwarded the caller's values instead of
            # dropping them.
            resp = prompt[0]["interruptResponse"]
            import json
            yield {"data": json.dumps({
                "interrupt_id": resp["interruptId"],
                "answers": resp["response"],
            })}
            yield {"result": _FakeResult("end_turn")}
        elif self._scripted.get("followup_questions"):
            itr = _FakeInterrupt("i-followup", {
                "questions_payload": {"name": "q2", "questions": []},
            })
            self._raise_interrupt(itr)
            yield {"result": _FakeResult("interrupt", [itr])}
        elif self._scripted.get("questions"):
            itr = _FakeInterrupt("i-strands", {
                "questions_payload": {"name": "q", "questions": []},
            })
            self._raise_interrupt(itr)
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
