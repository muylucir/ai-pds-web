# runner.py가 Discovery 드라이버에 요구하는 계약.
#
# runner.py는 세 메서드만 쓴다(run/run_answers/pending). 드라이버가 하나뿐이
# 됐어도 이 파일을 남기는 이유는 그 인터페이스를 글로 남겨 두는 자리가 필요하기
# 때문이다 — 예전에는 두 드라이버(StrandsDriver / ClaudeDriver)에 함께 걸어
# "기능 동등"을 증명하는 용도였고, 삭제된 sandbox_contract.py가 같은 패턴이었다.
#
# make_driver(scripted) 규약: scripted는 드라이버가 흉내낼 턴 대본이고,
# (driver, session) 튜플을 돌려준다. 대본의 형태는 SDK마다 다르므로 각
# 어댑터 테스트가 번역한다 — 이 모듈은 AgentEvent 출력만 본다.
#
# scripted 키 vocabulary (모든 어댑터가 이 키들을 이해해야 한다):
#   text: list[str]           — run()에서 각 항목이 kind=message로 온다.
#   tools: list[str]          — run()에서 도구 실행이 kind=status로 온다
#                                (연속 중복은 제거).
#   questions: bool           — run()에서 kind=questions가 interrupt_id를
#                                싣고 온다.
#   raise: bool                — run()에서 SDK 예외가 kind=error,
#                                text="agent turn failed"로 강등된다.
#   echo_answers: bool         — run_answers(interrupt_id, answers, session)가
#                                받은 그 두 값을 실제로 밑단 SDK 호출까지
#                                전달했는지 보기 위한 키. 이 대본을 받은
#                                드라이버는 자신이 받은 interrupt_id/answers를
#                                그대로 실은 kind=message 이벤트 하나를 내야
#                                한다 — text는 정확히
#                                `json.dumps({"interrupt_id": <받은 값>,
#                                "answers": <받은 값>})`. 어댑터마다 SDK에
#                                넘기는 prompt 모양은 다르므로(Strands는
#                                interruptResponse 리스트, Claude SDK는 다른
#                                모양일 수 있다) 이 모듈은 그 모양을 가정하지
#                                않고, 되돌려 받은 이 message로만 "값이
#                                전달됐다"를 확인한다.
#   followup_questions: bool   — run_answers 도중에도 새 인터럽트(후속 질문)가
#                                뜰 수 있음을 흉내낸다. run()의 questions와
#                                같은 모양(kind=questions + interrupt_id)으로
#                                오고, 턴은 done으로 끝난다 — runner.py:
#                                170-172가 답변 제출 턴 중에도 이 이벤트에서
#                                _pending_interrupt_id를 재포착하므로,
#                                run_answers가 questions를 낼 수 있어야 한다는
#                                것이 계약이다.
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
    await _assert_pending_returns_the_open_round(make_driver)
    await _assert_run_answers_is_exercised(make_driver)


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


async def _assert_pending_returns_the_open_round(make_driver) -> None:
    # 위 검사는 "안 뜬 질문은 None"만 본다. 그 반대 방향 — 질문이 떠 있으면
    # pending()이 그 라운드를 실제로 돌려준다 — 이 없으면 pending()을 통째로
    # None으로 만드는 회귀가 계약을 다 통과한다. 그런데 그건 새로고침 후
    # 답변(GET /pending → POST /answers)을 깨뜨린다:
    #
    #   runner.pending()은 payload에서 interrupt_id를 뽑아
    #   _pending_interrupt_id에 심고(runner.py:183-189), send_answers는 그 값이
    #   None이면 드라이버를 아예 부르지 않고 "no pending questions"로 거절한다
    #   (runner.py:158-160). 즉 pending()이 None이면 새로고침한 사용자는 답변을
    #   제출할 방법이 없다.
    #
    # 그래서 이 계약은 payload의 *모양*이 아니라 runner.py가 실제로 뽑아내는 그
    # 값(interrupt_id)만 요구한다 — 두 드라이버의 저장 방식은 서로 다르다
    # (ClaudeDriver는 인메모리 _pending_payload + S3 미러, StrandsDriver는
    # agent._interrupt_state). 어느 쪽이든 "직전 질문 라운드를 식별할 수 있어야
    # 한다"는 것은 동일하게 참이고, 실측으로 양쪽 모두 정직하게 만족한다.
    import json
    driver, session = make_driver({"questions": True})
    events = await _collect(driver.run("hi", session))
    q = [e for e in events if e.kind == "questions"]
    assert q, f"questions 이벤트가 없다: {[e.kind for e in events]}"
    raised = json.loads(q[0].payload or "{}").get("interrupt_id")

    payload = await driver.pending(session)
    assert payload is not None, (
        "질문이 떠 있는데 pending()이 None이다 — 새로고침 후 답변 제출이 "
        "runner.py:158-160에서 거절된다")
    got = json.loads(payload).get("interrupt_id")
    assert got == raised, (
        f"pending()이 방금 뜬 질문 라운드를 가리키지 않는다: {got!r} != {raised!r}")


async def _assert_run_answers_is_exercised(make_driver) -> None:
    # run_answers is one of the three contract methods runner.py actually
    # calls (runner.py:167, AgentRunner.send_answers) but nothing above ever
    # invokes it — a driver whose run_answers ignored interrupt_id/answers,
    # or crashed translating them into an SDK resume, would still pass every
    # check above. Two things runner.py genuinely depends on:
    import json

    # (a) run_answers reaches a terminal event (done or error) — send_answers'
    # loop depends on this to know when to sync the workspace to S3
    # (runner.py:172-174) — and the interrupt_id/answers the caller passed
    # actually make it to the underlying agent call. We can't assert on the
    # adapter's internal prompt shape (Strands builds an interruptResponse
    # list; other SDKs may differ) without breaking the "this module only
    # looks at AgentEvent output" invariant, so the scripted "echo_answers"
    # key asks the fake to echo back what its stream_async actually received,
    # as a message event — that keeps the assertion entirely on the observable
    # AgentEvent stream.
    driver, session = make_driver({"echo_answers": True})
    events = await _collect(driver.run_answers("i-42", {"1": "A"}, session))
    kinds = [e.kind for e in events]
    assert kinds[-1] in ("done", "error"), \
        f"run_answers가 종결 이벤트로 끝나지 않았다: {kinds}"
    echoed = []
    for e in events:
        if e.kind != "message" or not e.text:
            continue
        try:
            parsed = json.loads(e.text)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            echoed.append(parsed)
    match = next((p for p in echoed if p.get("interrupt_id") == "i-42"
                  and p.get("answers") == {"1": "A"}), None)
    assert match is not None, (
        "run_answers가 받은 interrupt_id/answers가 밑단 에이전트 호출까지 "
        f"전달되지 않았다: {events}")

    # (b) a follow-up question arriving during an answers turn still surfaces
    # as kind=questions with an interrupt_id — runner.py:170-172 re-captures
    # _pending_interrupt_id from exactly that event while inside
    # send_answers, so a multi-round question flow depends on run_answers
    # being able to yield questions too (not just run()).
    driver, session = make_driver({"followup_questions": True})
    events = await _collect(driver.run_answers("i-42", {"1": "A"}, session))
    kinds = [e.kind for e in events]
    assert kinds[-1] == "done", f"후속 질문 턴이 done으로 끝나지 않았다: {kinds}"
    q = [e for e in events if e.kind == "questions"]
    assert q, f"run_answers 중 후속 questions 이벤트가 없다: {kinds}"
    payload = json.loads(q[0].payload or "{}")
    assert payload.get("interrupt_id"), f"후속 질문에 interrupt_id 없음: {payload}"
