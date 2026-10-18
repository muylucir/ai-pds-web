# The contract runner.py requires of a Discovery driver.
#
# runner.py uses only three methods (run/run_answers/pending). Even though there is
# now only one driver, this file stays because the interface needs a place where it
# is written down. It used to be applied to both drivers (StrandsDriver / ClaudeDriver)
# to prove "feature parity"; the deleted sandbox_contract.py followed the same pattern.
#
# make_driver(scripted) convention: scripted is the turn script the driver imitates,
# and it returns a (driver, session) tuple. The script's shape differs per SDK, so each
# adapter test translates it; this module looks only at the AgentEvent output.
#
# scripted key vocabulary (every adapter must understand these keys):
#   text: list[str]           — in run(), each item arrives as kind=message.
#   tools: list[str]          — in run(), tool runs arrive as kind=status
#                                (consecutive duplicates removed).
#   questions: bool           — in run(), kind=questions arrives carrying
#                                interrupt_id.
#   raise: bool                — in run(), an SDK exception is downgraded to kind=error,
#                                text="agent turn failed".
#   echo_answers: bool         — a key for checking that run_answers(interrupt_id,
#                                answers, session) actually passed the two values it
#                                received all the way down to the underlying SDK call.
#                                A driver given this script must emit one kind=message
#                                event carrying exactly the interrupt_id/answers it
#                                received; text is exactly
#                                `json.dumps({"interrupt_id": <received value>,
#                                "answers": <received value>})`. The prompt shape
#                                passed to the SDK differs per adapter (Strands uses an
#                                interruptResponse list; the Claude SDK may use another
#                                shape), so this module does not assume that shape and
#                                confirms "the values were passed" only through this
#                                echoed message.
#   followup_questions: bool   — simulates that a new interrupt (follow-up questions)
#                                can appear even during run_answers. It arrives in the
#                                same shape as run()'s questions (kind=questions +
#                                interrupt_id), and the turn ends with done. Because
#                                runner.py:170-172 re-captures _pending_interrupt_id
#                                from this event even during an answer-submission turn,
#                                it is part of the contract that run_answers can emit
#                                questions.
from __future__ import annotations

from aipds.models import AgentEvent


async def _collect(agen) -> list[AgentEvent]:
    return [ev async for ev in agen]


async def assert_driver_contract(make_driver) -> None:
    """The whole contract. On failure, the message shows which item failed."""
    await _assert_text_turn(make_driver)
    await _assert_tool_status(make_driver)
    await _assert_questions_carry_an_interrupt_id(make_driver)
    await _assert_failure_is_sanitized(make_driver)
    await _assert_pending_is_none_when_nothing_pends(make_driver)
    await _assert_pending_returns_the_open_round(make_driver)
    await _assert_run_answers_is_exercised(make_driver)


async def _assert_text_turn(make_driver) -> None:
    # The most basic case: model text arrives as kind=message, the end of the turn
    # as kind=done.
    driver, session = make_driver({"text": ["안녕하세요"]})
    events = await _collect(driver.run("hi", session))
    kinds = [e.kind for e in events]
    assert "message" in kinds, f"텍스트가 message로 오지 않았다: {kinds}"
    assert kinds[-1] == "done", f"턴이 done으로 끝나지 않았다: {kinds}"
    assert any(e.text == "안녕하세요" for e in events if e.kind == "message")


async def _assert_tool_status(make_driver) -> None:
    # Tool runs arrive as kind=status, and consecutive runs of the same tool arrive
    # once (the SDK emits a frame per delta, so deduplication is part of the
    # contract). The tool name itself is an arbitrary string that differs per SDK
    # (Strands uses real tool names like file_read; the Claude SDK adapter may use
    # other names), so we only check that the value the script gave comes back
    # unchanged; no specific name is hardcoded.
    driver, session = make_driver({"tools": ["A", "A", "B"]})
    events = await _collect(driver.run("hi", session))
    statuses = [e.text for e in events if e.kind == "status"]
    assert statuses == ["A", "B"], f"status 중복 제거 실패: {statuses}"


async def _assert_questions_carry_an_interrupt_id(make_driver) -> None:
    # runner.py extracts interrupt_id from the payload and passes it to send_answers
    # (_interrupt_id_from). Without it, the answer-submission path dies.
    import json
    driver, session = make_driver({"questions": True})
    events = await _collect(driver.run("hi", session))
    q = [e for e in events if e.kind == "questions"]
    assert q, "questions 이벤트가 없다"
    payload = json.loads(q[0].payload or "{}")
    assert payload.get("interrupt_id"), f"interrupt_id 없음: {payload}"
    assert payload.get("questions"), f"questions 본문 없음: {payload}"


async def _assert_failure_is_sanitized(make_driver) -> None:
    # If an SDK exception leaks as is, the stack trace reaches the user's screen.
    # Downgrade it to a fixed string (a contract string that tests match partially).
    driver, session = make_driver({"raise": True})
    events = await _collect(driver.run("hi", session))
    errors = [e for e in events if e.kind == "error"]
    assert errors, f"실패가 error로 오지 않았다: {[e.kind for e in events]}"
    assert errors[0].text == "agent turn failed"


async def _assert_pending_is_none_when_nothing_pends(make_driver) -> None:
    driver, session = make_driver({"text": ["ok"]})
    assert await driver.pending(session) is None


async def _assert_pending_returns_the_open_round(make_driver) -> None:
    # The check above only verifies "a question that is not open is None". Without
    # the opposite direction (when a question is open, pending() actually returns
    # that round), a regression that makes pending() None across the board passes
    # the whole contract. But that breaks answering after a reload (GET /pending →
    # POST /answers):
    #
    #   runner.pending() extracts interrupt_id from the payload and plants it in
    #   _pending_interrupt_id (runner.py:183-189), and if that value is None, send_answers
    #   does not call the driver at all and rejects with "no pending questions"
    #   (runner.py:158-160). That is, if pending() is None, a user who reloaded has no way
    #   to submit answers.
    #
    # So this contract requires not the *shape* of the payload but only the value
    # runner.py actually extracts (interrupt_id). The two drivers store it
    # differently (ClaudeDriver: in-memory _pending_payload + S3 mirror;
    # StrandsDriver: agent._interrupt_state). Either way, "the previous question
    # round must be identifiable" holds equally, and as observed both sides satisfy
    # it honestly.
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
