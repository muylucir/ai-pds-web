# 두 드라이버(StrandsDriver / ClaudeDriver)가 공유하는 계약.
#
# runner.py는 세 메서드만 쓴다(runner.py:129,167,183). 그 계약을 여기 한 곳에
# 두고 양쪽에 걸면 "기능 동등"을 기계적으로 증명할 수 있다 — 삭제된
# sandbox_contract.py가 같은 패턴이었다.
#
# make_driver(scripted) 규약: scripted는 드라이버가 흉내낼 턴 대본이고,
# (driver, session) 튜플을 돌려준다. 대본의 형태는 SDK마다 다르므로 각
# 어댑터 테스트가 번역한다 — 이 모듈은 AgentEvent 출력만 본다.
from __future__ import annotations

from pathfinder.models import AgentEvent


async def _collect(agen) -> list[AgentEvent]:
    return [ev async for ev in agen]


async def assert_driver_contract(make_driver) -> None:
    """계약 전체. 실패 시 어느 항목인지 메시지로 드러난다."""
    await _assert_text_turn(make_driver)
    await _assert_tool_status(make_driver)
    await _assert_questions_carry_an_interrupt_id(make_driver)
    await _assert_failure_is_sanitized(make_driver)
    await _assert_pending_is_none_when_nothing_pends(make_driver)


async def _assert_text_turn(make_driver) -> None:
    # 가장 기본: 모델 텍스트가 kind=message로, 턴 끝이 kind=done으로 온다.
    driver, session = make_driver({"text": ["안녕하세요"]})
    events = await _collect(driver.run("hi", session))
    kinds = [e.kind for e in events]
    assert "message" in kinds, f"텍스트가 message로 오지 않았다: {kinds}"
    assert kinds[-1] == "done", f"턴이 done으로 끝나지 않았다: {kinds}"
    assert any(e.text == "안녕하세요" for e in events if e.kind == "message")


async def _assert_tool_status(make_driver) -> None:
    # 도구 실행은 kind=status로 오고, 같은 도구가 연속되면 한 번만 온다
    # (SDK가 델타마다 프레임을 내므로 중복 제거가 계약이다). 도구 이름
    # 자체는 SDK마다 다른 임의 문자열이므로(Strands는 file_read 같은 실제
    # 도구 이름, Claude SDK 어댑터는 다른 이름을 쓸 수 있다) 대본이 준 값을
    # 그대로 되돌려주는지만 본다 — 특정 이름을 하드코딩하지 않는다.
    driver, session = make_driver({"tools": ["A", "A", "B"]})
    events = await _collect(driver.run("hi", session))
    statuses = [e.text for e in events if e.kind == "status"]
    assert statuses == ["A", "B"], f"status 중복 제거 실패: {statuses}"


async def _assert_questions_carry_an_interrupt_id(make_driver) -> None:
    # runner.py가 payload에서 interrupt_id를 뽑아 send_answers에 넘긴다
    # (_interrupt_id_from). 없으면 답변 제출 경로가 죽는다.
    import json
    driver, session = make_driver({"questions": True})
    events = await _collect(driver.run("hi", session))
    q = [e for e in events if e.kind == "questions"]
    assert q, "questions 이벤트가 없다"
    payload = json.loads(q[0].payload or "{}")
    assert payload.get("interrupt_id"), f"interrupt_id 없음: {payload}"
    assert payload.get("questions"), f"questions 본문 없음: {payload}"


async def _assert_failure_is_sanitized(make_driver) -> None:
    # SDK 예외가 그대로 새면 스택트레이스가 사용자 화면에 간다. 정해진 문자열로
    # 강등한다(테스트가 부분 매칭하는 계약 문자열).
    driver, session = make_driver({"raise": True})
    events = await _collect(driver.run("hi", session))
    errors = [e for e in events if e.kind == "error"]
    assert errors, f"실패가 error로 오지 않았다: {[e.kind for e in events]}"
    assert errors[0].text == "agent turn failed"


async def _assert_pending_is_none_when_nothing_pends(make_driver) -> None:
    driver, session = make_driver({"text": ["ok"]})
    assert await driver.pending(session) is None
