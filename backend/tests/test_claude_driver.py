# ClaudeDriver behavior specific to it, outside the contract (driver_contract.py).
#
# Question scripts use tests/fakes/fake_sdk_asking.AskingSdkClient — like the real SDK,
# it calls the driver's can_use_tool callback in a separate task, and meanwhile
# receive_response() yields nothing. That callback is the only path that produces a
# questions event, so a fake that puts an AskUserQuestion ToolUseBlock in the script
# would let none of this file's question tests take the real path (see that module for
# the detailed rationale).
import asyncio
import json

import pytest

from aipds.agent.claude_driver import ClaudeDriver
from aipds.agent.pending_store import PENDING_KEY, save_pending
from aipds.models import AgentEvent
from tests.fakes.fake_sdk_asking import (
    PREFACE_TEXT, cancel_pending_callbacks, sdk_client_for,
)
from tests.fakes.in_memory_s3 import FakeS3Store


@pytest.fixture(autouse=True)
def _cleanup_parked_callbacks():
    """A test that ends without answering a question leaves a parked can_use_tool task
    behind — it leaks out as "Task was destroyed but it is pending!" when the loop
    closes, so it is cleared after each test."""
    yield
    cancel_pending_callbacks()


@pytest.fixture(autouse=True)
def _legacy_question_path(monkeypatch):
    """This file verifies the **AskUserQuestion path** — on 2026-08-17 the default was
    flipped and that path became the escape hatch (see the comment on
    claude_driver.FILE_QUESTIONS_ENV).

    The escape hatch has to stay alive, so its verification has to stay alive too. Why it
    is turned off explicitly here instead of relying on the default: so that when the
    default changes, this file does not end up "silently verifying something else" — that
    actually happened, and that is why this fixture exists.
    """
    monkeypatch.setenv("AIPDS_FILE_QUESTIONS", "false")


def _driver(tmp_path, scripted, s3=None):
    rules = tmp_path / "rules" / "aws-aiplc-rules"
    rules.mkdir(parents=True)
    (rules / "core-workflow.md").write_text("WORKFLOW", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    captured: dict = {}

    d = ClaudeDriver(workspace=str(ws), rules_dir=str(tmp_path / "rules"),
                     config_dir=str(tmp_path / "cfg"), s3=s3 or FakeS3Store(),
                     client_factory=lambda session: None)

    def factory(session):
        captured["session"] = session
        client = sdk_client_for(scripted, d._on_can_use_tool)
        captured["client"] = client
        return client

    d._client_factory = factory  # type: ignore[assignment]
    return d, ws, captured


def _runner(tmp_path, scripted, s3=None):
    """The driver behind the REAL runner.AgentRunner.

    The abandonment tests below go through it rather than calling the driver
    directly, because `runner.py:144-152` is what actually abandons this
    generator in production (SSE disconnect, proxy timeout, navigation) and it
    wraps the driver in one more generator layer — which is exactly what
    `GeneratorExit` has to propagate through. A driver-only test would not
    exercise that layer.
    """
    from aipds.runner import AgentRunner

    s3 = s3 or FakeS3Store()
    d, ws, cap = _driver(tmp_path, scripted, s3=s3)
    runner = AgentRunner(project_id="p1", driver=d, s3=s3, local_root=ws,
                         session={"session_id": "s-1"})
    return d, runner, cap


async def _reconnect_gap() -> None:
    """The event-loop ticks a real reconnect takes.

    `aclose()` runs only the OUTERMOST generator's `finally` synchronously, so
    `GeneratorExit` needs a few ticks to reach the driver's nested generators
    (round 1's IMPORTANT 3 measured the same thing). A browser reconnect is a
    network round trip, so this is not an artificial delay.
    """
    for _ in range(4):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_places_the_rules_before_the_first_turn(tmp_path):
    # Without the rules, the agent runs without knowing the workflow.
    d, ws, _ = _driver(tmp_path, {"text": ["ok"]})
    [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    # CLAUDE.md is now an assembly: the language directive, then the workflow. The directive
    # comes from code (agent/workspace_rules.LANGUAGE_DIRECTIVES), not from a fixture.
    text = (ws / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.index("언어 규약") < text.index("WORKFLOW")


@pytest.mark.asyncio
async def test_persists_pending_questions_to_s3(tmp_path):
    # The basis for restoring the form after a refresh. An in-memory Future alone cannot survive a restart.
    s3 = FakeS3Store()
    d, _, _ = _driver(tmp_path, {"questions": True}, s3=s3)
    [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    assert PENDING_KEY in s3.blobs
    saved = json.loads(s3.blobs[PENDING_KEY])
    assert saved["interrupt_id"]
    assert saved["sdk_questions"]  # needed to translate the answers back


@pytest.mark.asyncio
async def test_persists_the_submitted_answers_as_a_record(tmp_path):
    """The basis for keeping restoration independent of CLI prose.

    The answer result the CLI leaves in the transcript is an English sentence it composes
    itself (`Your questions have been answered: "질문"="라벨"`), and our question numbers and
    option letters are not in it. So the exact values are recorded at the moment the answers
    arrive — the approval gate already made the same decision (08aaa85). The key is the
    tool_use_id, and with that value restoration joins exactly against the transcript's
    tool_result.
    """
    s3 = FakeS3Store()
    d, _, cap = _driver(tmp_path, {"questions": True}, s3=s3)
    events = [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    iid = json.loads(next(e.payload for e in events if e.kind == "questions"))[
        "interrupt_id"]

    [ev async for ev in d.run_answers(iid, {"1": "A", "2": "B,C"},
                                      {"session_id": "s-1"})]

    key = f"answers/{cap['client'].tool_use_id}.json"
    assert key in s3.blobs, list(s3.blobs)
    saved = json.loads(s3.blobs[key])
    assert saved["answers"] == {"1": "A", "2": "B,C"}
    assert saved["interrupt_id"] == iid
    # The question payload is stored alongside — the answer values are letters, so expanding
    # them into option text needs the payload as it was at that moment.
    assert saved["questions"]["questions"], saved["questions"]


#: A question file containing **the same sentence** as the question the fake asks
#: (fake_sdk_asking.DEFAULT_SDK_QUESTIONS). Recording back matches on the question text, so
#: if this sentence drifts, nothing matches.
_QUESTION_FILE_MD = """## Question 1
다음 단계는?

A) 진행
B) 종료
X) Other (please describe after [Answer]: tag below)

[Answer]:
"""


@pytest.mark.asyncio
async def test_answers_are_recorded_in_the_question_file(tmp_path):
    """**This is the core contract of the ai-plc workflow.**

    The upstream rules treat the question file as the answer sheet — question-format-guide.md
    instructs "Extract answers after [Answer]: tags", and session-continuity.md:31-33 says to
    read that file when resuming a stage. If the answers are not written in, the resumed
    session loses the user's decisions. If the wiring breaks, only empty blanks remain with no
    error, so it is pinned down here.
    """
    s3 = FakeS3Store()
    d, _, _ = _driver(tmp_path, {"questions": True}, s3=s3)
    qpath = tmp_path / "ws" / "aiplc-docs" / "strategy-questions.md"
    qpath.parent.mkdir(parents=True)
    qpath.write_text(_QUESTION_FILE_MD, encoding="utf-8")

    events = [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    iid = json.loads(next(e.payload for e in events if e.kind == "questions"))[
        "interrupt_id"]
    later = [ev async for ev in d.run_answers(iid, {"1": "B"},
                                              {"session_id": "s-1"})]

    assert "[Answer]: B" in qpath.read_text(encoding="utf-8")
    # **It must reach S3 too.** runner.read_file reads from S3 (runner.py:55), so writing
    # only locally means the screen and the next stage see empty blanks. On top of that,
    # _restore_workspace_from_s3 at the start of every turn is "S3 always wins", so if a turn
    # is abandoned without terminating, the next turn overwrites the local answers with the
    # empty file from S3.
    key = "aiplc-docs/strategy-questions.md"
    assert key in s3.blobs, list(s3.blobs)
    assert "[Answer]: B" in s3.blobs[key]
    # It also emits file_changed so the artifact panel on screen refreshes — if only the file
    # changes and there is no event, the user has to refresh to see the answer filled in.
    assert ("aiplc-docs/strategy-questions.md"
            in [e.path for e in later if e.kind == "file_changed"])


@pytest.mark.asyncio
async def test_a_missing_question_file_does_not_fail_the_answers_turn(tmp_path):
    """Having no question file is normal (the prototype path, etc.). The turn must not die."""
    d, _, _ = _driver(tmp_path, {"questions": True})
    events = [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    iid = json.loads(next(e.payload for e in events if e.kind == "questions"))[
        "interrupt_id"]

    later = [ev async for ev in d.run_answers(iid, {"1": "A"},
                                              {"session_id": "s-1"})]

    assert later and later[-1].kind == "done", [(e.kind, e.text) for e in later]
    assert not [e for e in later if e.kind == "file_changed"]


@pytest.mark.asyncio
async def test_answer_record_failure_does_not_fail_the_turn(tmp_path):
    """The record is a restoration convenience — an S3 hiccup killing the turn that was just answered would be worse."""
    class _PutBoom(FakeS3Store):
        async def put(self, key, content):
            if key.startswith("answers/"):
                raise RuntimeError("s3 down")
            return await super().put(key, content)

    s3 = _PutBoom()
    d, _, _ = _driver(tmp_path, {"questions": True}, s3=s3)
    events = [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    iid = json.loads(next(e.payload for e in events if e.kind == "questions"))[
        "interrupt_id"]

    later = [ev async for ev in d.run_answers(iid, {"1": "A"},
                                              {"session_id": "s-1"})]

    assert later and later[-1].kind == "done", [(e.kind, e.text) for e in later]


@pytest.mark.asyncio
async def test_the_question_turn_ends_so_answers_can_be_submitted(tmp_path):
    # A question turn must always end with a terminal event after questions. Two things hang
    # on this: the frontend refuses to submit answers while the stream is open
    # (useWorkspaceStream.ts:230), and runner uploads the workspace to S3 only on done/error
    # (runner.py:134-140) — a turn stuck on a question leaves the files the agent already
    # wrote only on volatile local disk.
    d, _, _ = _driver(tmp_path, {"questions": True})
    kinds = [e.kind async for e in d.run("hi", {"session_id": "s-1"})]
    assert "questions" in kinds
    assert kinds[-1] == "done"


@pytest.mark.asyncio
async def test_the_prose_before_a_question_is_not_lost(tmp_path):
    # The real CLI writes the model's "why I am asking" explanation (an assistant message)
    # and the control_request back to back in one pass of its read loop (query.py:250-322) —
    # with no model latency in between. And _CONTACT_ADDENDUM:44-45 in driver.py requires an
    # explanation before a question. So this is not a rare race but the normal path, every
    # time.
    #
    # If it is lost, the user sees only a question card with no explanation. In the old
    # structure, message receipt was a cancellable future, and anyio's send_nowait hands the
    # item directly to a parked receiver without going through the buffer
    # (memory.py:220-231), so the moment that future was cancelled the message was gone for
    # good. Now a separate reader task owns receipt.
    d, _, _ = _driver(tmp_path, {"questions": True})
    events = [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    kinds = [e.kind for e in events]
    assert kinds == ["message", "questions", "done"], kinds
    assert next(e.text for e in events if e.kind == "message") == PREFACE_TEXT


@pytest.mark.asyncio
async def test_every_message_buffered_before_a_question_survives(tmp_path):
    # The test above only covers "one", and that is why it looked done in round 1. A
    # structure that consumes one at a time and drops the rest breaks here. Observed: the gap
    # between messages from the real CLI is 3-4ms, within the driver's 50ms poll, so several
    # arriving in one pass is routine.
    texts = ["첫 문장", "둘째 문장", "셋째 문장"]
    d, _, _ = _driver(tmp_path, {"questions": True, "preface_texts": texts})
    events = [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    assert [e.text for e in events if e.kind == "message"] == texts
    assert [e.kind for e in events][-2:] == ["questions", "done"]


@pytest.mark.asyncio
async def test_a_message_arriving_while_the_question_is_yielded_survives(tmp_path):
    # At the point where questions is yielded, the driver is suspended exactly on that
    # yield — instead of guessing where it is, we observe from the consumer side. The fake in
    # the previous round found this window with a predicate that polled driver state, and
    # that predicate had false positives in both directions (True even while
    # _on_can_use_tool was saving to S3, True even after the drain had finished) — so the
    # test could report "hit the window" even when it had become vacuous.
    #
    # In production this window is the time it takes to write one SSE frame (runner.py
    # awaits on every frame). A message the CLI sends in that interval must not be lost.
    d, _, cap = _driver(tmp_path, {"questions": True})
    events = []
    async for ev in d.run("hi", {"session_id": "s-1"}):
        events.append(ev)
        if ev.kind == "questions":
            cap["client"].deliver_late("질문 yield 중 도착")
            await asyncio.sleep(0)
    kinds = [e.kind for e in events]
    texts = [e.text for e in events if e.kind == "message"]
    assert "질문 yield 중 도착" in texts, kinds
    # It comes after questions — it was handed over after that point, so only the final
    # harvest before termination can pick it up. The ordering itself is proof that the
    # harvest worked.
    assert kinds.index("questions") < kinds.index("message", 1), kinds
    assert kinds[-1] == "done"
    assert kinds.count("done") == 1, kinds


@pytest.mark.asyncio
async def test_a_message_arriving_during_the_post_done_sync_is_not_destroyed(tmp_path):
    # While suspended on `yield done`, runner.py:134-140 awaits the real S3 sync (tens to
    # hundreds of ms). In the old structure, a message the CLI sent during that interval was
    # handed directly to the parked receiver and destroyed by the cancellation after the
    # consumer came back.
    #
    # The fix is structural, not timing: a separate reader task owns message receipt, so no
    # matter where or for how many seconds _pump is suspended, arriving messages pile up in
    # the reader's inbox, and the answer turn that takes over the same reader relays them.
    d, _, cap = _driver(tmp_path, {"questions": True})
    events = []
    async for ev in d.run("hi", {"session_id": "s-1"}):
        events.append(ev)
        if ev.kind == "done":
            # What production does: sees the terminal event and awaits the S3 sync.
            await asyncio.sleep(0.02)
            cap["client"].deliver_late("동기화 중 도착")
            await asyncio.sleep(0.02)
    assert [e.kind for e in events] == ["message", "questions", "done"]

    # The answer turn must pick up that message as is — *not* by opening a new iterator, but
    # by taking over the question turn's very reader (_continue_after_answers). This
    # distinction is the most expensive lesson in this file: the model that "opening a new
    # receive_response() can read what anyio buffered" was disproved by round 4 (send_nowait
    # hands the item directly to a parked receiver and does not buffer it — cancelling
    # destroys the item), and that belief produced message-loss defects three rounds in a
    # row. The current code opens only one iterator per turn (claude_driver.py:824) and keeps
    # it for the whole question round trip.
    iid = json.loads(next(e.payload for e in events if e.kind == "questions"))[
        "interrupt_id"]
    later = [ev async for ev in d.run_answers(iid, {"1": "A"},
                                              {"session_id": "s-1"})]
    assert "동기화 중 도착" in [e.text for e in later if e.kind == "message"], \
        [(e.kind, e.text) for e in later]


@pytest.mark.asyncio
async def test_a_question_turn_yields_exactly_one_terminal_event(tmp_path):
    # If the final sweep consumes the ResultMessage, _translate produces done, and then the
    # terminal yield emits a second done. Then runner.py:134 uploads the entire workspace to
    # S3 twice, and the SSE client receives two terminal events.
    d, _, _ = _driver(tmp_path, {"questions": True,
                                 "result_with_question": True})
    kinds = [e.kind async for e in d.run("hi", {"session_id": "s-1"})]
    assert kinds.count("done") == 1, kinds
    assert kinds[-1] == "done"


# ---- A turn failure reported by the CLI (ResultMessage.is_error) ----
#
# The CLI does not throw a turn failure as an exception. It puts its own error text ("API
# Error: The system encountered an unexpected error during processing") in an
# AssistantMessage and sends a ResultMessage carrying is_error=True. If the driver does not
# read that flag, the failure renders as a normal answer — the user sees an English error
# message tacked onto a Korean answer, the agent keeps retrying the same step, and nothing
# is left in our logs (since every field that explains the failure is discarded).

def _failing_client(scripted, can_use_tool, *, status=500):
    """A turn that ends in failure: an is_error ResultMessage after the body message."""
    from tests.fakes.fake_sdk import (AssistantMessage, FakeSdkClient,
                                      ResultMessage, TextBlock)
    blocks = [TextBlock(text=t) for t in scripted.get("text", [])]
    msgs = ([AssistantMessage(content=blocks)] if blocks else []) + [
        ResultMessage(is_error=True, api_error_status=status,
                      terminal_reason="completed", errors=["boom"]),
    ]
    return FakeSdkClient(msgs)


def _driver_with_failing_turn(tmp_path, scripted, *, status=500):
    rules = tmp_path / "rules" / "aws-aiplc-rules"
    rules.mkdir(parents=True)
    (rules / "core-workflow.md").write_text("WORKFLOW", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    d = ClaudeDriver(workspace=str(ws), rules_dir=str(tmp_path / "rules"),
                     config_dir=str(tmp_path / "cfg"), s3=FakeS3Store(),
                     client_factory=lambda session: None)
    d._client_factory = lambda session: _failing_client(  # type: ignore[assignment]
        scripted, d._on_can_use_tool, status=status)
    return d


@pytest.mark.asyncio
async def test_a_cli_reported_failure_ends_the_turn_with_error_not_done(tmp_path):
    # The symptom this defect actually caused: the PR/FAQ step kept failing while the screen
    # looked normal. sse.ts:29 closes the EventSource on done and never takes the error
    # branch at useWorkspaceStream.ts:154, so the failure masquerades as success.
    d = _driver_with_failing_turn(tmp_path, {"text": ["작성하겠습니다"]})
    events = [e async for e in d.run("hi", {"session_id": "s-1"})]
    kinds = [e.kind for e in events]
    assert kinds[-1] == "error", kinds
    assert "done" not in kinds, kinds


@pytest.mark.asyncio
async def test_a_failed_turn_still_relays_what_the_agent_produced(tmp_path):
    # Even on failure, the body produced up to that point must reach the user — how far the
    # agent got is the basis for deciding whether to retry.
    d = _driver_with_failing_turn(tmp_path, {"text": ["요구사항을 정리했습니다"]})
    events = [e async for e in d.run("hi", {"session_id": "s-1"})]
    assert "요구사항을 정리했습니다" in [e.text for e in events if e.kind == "message"]


@pytest.mark.asyncio
async def test_a_failed_turn_yields_exactly_one_terminal_event(tmp_path):
    # _pump's invariant 1 holds on the failure path too: exactly one terminal event,
    # always last. Only its kind changes from done to error.
    d = _driver_with_failing_turn(tmp_path, {"text": ["본문"]})
    kinds = [e.kind async for e in d.run("hi", {"session_id": "s-1"})]
    assert kinds.count("error") == 1, kinds
    assert kinds.count("done") == 0, kinds


@pytest.mark.asyncio
async def test_the_failure_text_tells_the_user_to_retry(tmp_path):
    # The common causes (Bedrock 429/529) are transient, so retrying works. The HTTP status
    # goes to the log and is not shown on screen — it is nothing a workshop participant can act on.
    d = _driver_with_failing_turn(tmp_path, {"text": ["본문"]}, status=429)
    events = [e async for e in d.run("hi", {"session_id": "s-1"})]
    text = next(e.text for e in events if e.kind == "error")
    assert "다시 시도" in text, text
    assert "429" not in text, text


@pytest.mark.asyncio
async def test_the_api_error_status_is_logged_for_diagnosis(tmp_path, caplog):
    # This value being discarded is what kept the cause from being found. It is the only
    # basis for telling 429/529 (transient) apart from 500 (not transient).
    import logging
    d = _driver_with_failing_turn(tmp_path, {"text": ["본문"]}, status=529)
    with caplog.at_level(logging.ERROR):
        [e async for e in d.run("hi", {"session_id": "s-1"})]
    assert "529" in caplog.text, caplog.text


@pytest.mark.asyncio
async def test_a_successful_turn_is_unaffected(tmp_path):
    # Regression guard: when is_error is absent or False, it is done as before.
    d, _, _ = _driver(tmp_path, {"text": ["본문"]})
    kinds = [e.kind async for e in d.run("hi", {"session_id": "s-1"})]
    assert kinds[-1] == "done", kinds
    assert "error" not in kinds, kinds


# ---- An interrupted turn is not a failure ----

@pytest.mark.asyncio
async def test_an_interrupted_turn_ends_with_done_not_error(tmp_path):
    """An interrupt is something the user did, not a failure of the turn.

    The CLI reports an interrupted turn as ResultMessage(is_error=True,
    terminal_reason="aborted_streaming") — SDK types.py:1249-1257 defines those two values
    ("aborted_streaming"/"aborted_tools") as the signal of a turn cancelled by interrupt().
    Looking only at is_error makes "이번 턴이 실패했습니다" (this turn failed) appear as the
    result of the stop button the user just pressed.

    A defect found by a real-CLI probe. The fake-SDK tests did not script this combination
    after an interrupt, so the unit tests did not reveal it.
    """
    d, _, _ = _driver(tmp_path, {"result_is_error": True,
                                 "result_terminal_reason": "aborted_streaming"})
    kinds = [ev.kind async for ev in d.run("hi", {"session_id": "s-1"})]
    assert kinds[-1] == "done", kinds
    assert not any(k == "error" for k in kinds), kinds


@pytest.mark.asyncio
async def test_aborted_tools_is_also_not_a_failure(tmp_path):
    """An interrupt during tool execution is the same — the SDK defines the two values side by
    side. Handling only one means a stop pressed while a tool was running still shows as a failure."""
    d, _, _ = _driver(tmp_path, {"result_is_error": True,
                                 "result_terminal_reason": "aborted_tools"})
    kinds = [ev.kind async for ev in d.run("hi", {"session_id": "s-1"})]
    assert kinds[-1] == "done", kinds


@pytest.mark.asyncio
async def test_a_genuine_failure_still_reports_error(tmp_path):
    """Regression guard. This branch is the code that fixed a bug that swallowed failures
    (the history at claude_driver.py:888-899) — Bedrock 429/500/529 and a deadlocked tool
    must still go to error. Only interrupts are carved out as an exception; the branch is not
    disabled."""
    d, _, _ = _driver(tmp_path, {"result_is_error": True,
                                 "result_terminal_reason": "completed"})
    kinds = [ev.kind async for ev in d.run("hi", {"session_id": "s-1"})]
    assert kinds[-1] == "error", kinds


@pytest.mark.asyncio
async def test_a_failure_without_a_terminal_reason_still_reports_error(tmp_path):
    """Older CLIs do not send terminal_reason (SDK docs). Then is_error is the only basis for
    a decision, so it is treated as a failure as before — reading None as "might be an
    interrupt" would let a genuine failure quietly go out as done."""
    d, _, _ = _driver(tmp_path, {"result_is_error": True})
    kinds = [ev.kind async for ev in d.run("hi", {"session_id": "s-1"})]
    assert kinds[-1] == "error", kinds


@pytest.mark.asyncio
async def test_the_final_message_of_a_turn_is_translated_only_once(tmp_path):
    # The terminal path harvests repeatedly until both sources are exhausted. If a message is
    # not popped from the inbox exactly once, it enters _translate twice, and the side effects
    # the message carries (stage/document/file_changed) run twice. That is why we count
    # _translate calls rather than the output — it catches the entire class of side effects.
    d, _, _ = _driver(tmp_path, {"text": ["본문"]})
    seen: list[str] = []
    original = d._translate

    def counting(msg, reader=None):
        seen.append(type(msg).__name__)
        return original(msg, reader)

    d._translate = counting  # type: ignore[method-assign]
    kinds = [e.kind async for e in d.run("hi", {"session_id": "s-1"})]
    assert kinds[-1] == "done"
    assert seen.count("ResultMessage") == 1, seen


@pytest.mark.asyncio
async def test_queued_tool_events_are_emitted_before_the_terminal_event(tmp_path):
    # sse.ts:29 closes the EventSource on the done frame, so frames after done never reach
    # onEvent — a stage silently disappears (useWorkspaceStream.ts:134-137 cannot add it to
    # stages), and a document leaves the document panel stale.
    #
    # Tools run in the SDK's own task, so they can emit at any moment the driver is suspended
    # on a yield. Here the emit happens at the very moment the terminal harvest yields a
    # message — that pass's intermediate drain has already gone by, so unless the terminal
    # path repeats until the queue is exhausted, this event is pushed behind done or lost.
    d, _, cap = _driver(tmp_path, {"questions": True})
    kinds = []
    async for ev in d.run("hi", {"session_id": "s-1"}):
        kinds.append(ev.kind)
        if ev.kind == "questions":
            cap["client"].deliver_late("종결 수확이 실어 낼 메시지")
            await asyncio.sleep(0)
        elif ev.kind == "message" and len(kinds) > 1:
            # The driver is now suspended on the terminal harvest's yield.
            d._emit(AgentEvent(kind="stage", payload='{"name": "stage-1"}'))
    assert "stage" in kinds, kinds
    assert kinds.index("stage") < kinds.index("done"), kinds
    assert kinds[-1] == "done"
    assert kinds.count("done") == 1, kinds


@pytest.mark.asyncio
async def test_a_message_arriving_while_a_queued_tool_event_is_yielded_survives(
        tmp_path):
    # The window created by round 3's ordering fix: when tool events come in the same burst as
    # a question (Write/Edit → file_changed, report_stage → stage — the normal shape of a
    # Discovery turn), the driver ends up yielding inside the drain just before termination.
    # In the old structure the sweep right before had parked a receive, so a message arriving
    # during that yield was handed to a receiver about to be cancelled and was lost for good —
    # it reproduced with just the time it takes to write one SSE frame (sleep(0)), without
    # even needing S3 latency.
    #
    # Now message receipt is owned by a separate reader task rather than a cancellable
    # future, so no interruption of _pump can destroy a message. That property is pinned here.
    d, _, cap = _driver(tmp_path, {"questions": True})
    events = []
    async for ev in d.run("hi", {"session_id": "s-1"}):
        events.append(ev)
        if ev.kind == "questions":
            d._emit(AgentEvent(kind="file_changed", path="doc.md"))
        elif ev.kind == "file_changed":
            cap["client"].deliver_late("중단 직전 도착")
            await asyncio.sleep(0)
    kinds = [e.kind for e in events]
    assert "file_changed" in kinds, kinds
    assert kinds.index("file_changed") < kinds.index("done"), kinds
    assert kinds[-1] == "done"
    # Whether it goes out in this turn or in the answer turn, it just must not be lost — a
    # loss means a question card with no explanation.
    iid = json.loads(next(e.payload for e in events if e.kind == "questions"))[
        "interrupt_id"]
    later = [ev async for ev in d.run_answers(iid, {"1": "A"},
                                              {"session_id": "s-1"})]
    texts = [e.text for e in list(events) + later if e.kind == "message"]
    assert "중단 직전 도착" in texts, [(e.kind, e.text) for e in events + later]


@pytest.mark.asyncio
async def test_a_message_arriving_as_the_turn_is_abandoned_survives(tmp_path):
    # runner.py:144-152 takes this path routinely (SSE disconnect, proxy timeout, the user
    # leaving). In the old structure, cancellation landed at the point of interruption, so
    # whatever arrived after the last arming was already in the future and died with it —
    # retire() in finally cannot undo what was already handed over. When the user reconnects,
    # GET /pending returns the still-live question, and run_answers sees only that question
    # card with no explanation.
    d, _, cap = _driver(tmp_path, {"questions": True})
    events = []
    agen = d.run("hi", {"session_id": "s-1"}).__aiter__()
    async for ev in agen:
        events.append(ev)
        if ev.kind == "questions":
            cap["client"].deliver_late("중단 직전 도착")
            await asyncio.sleep(0)      # let the reader actually take it in
            break
    await agen.aclose()                 # the consumer goes away
    # The CLI keeps sending even while there is no consumer.
    cap["client"].deliver_late("중단 이후 도착")
    await asyncio.sleep(0)

    iid = json.loads(next(e.payload for e in events if e.kind == "questions"))[
        "interrupt_id"]
    later = [ev async for ev in d.run_answers(iid, {"1": "A"},
                                              {"session_id": "s-1"})]
    texts = [e.text for e in later if e.kind == "message"]
    assert "중단 직전 도착" in texts, [(e.kind, e.text) for e in later]
    assert "중단 이후 도착" in texts, [(e.kind, e.text) for e in later]
    assert [e.kind for e in later][-1] == "done"


@pytest.mark.asyncio
async def test_messages_not_yet_yielded_survive_abandonment(tmp_path):
    # A regression introduced by round 4. Moving ownership from the anyio buffer to the
    # driver was right, but because the whole inbox was batch-popped into a local list and
    # then yielded, the rest that had not yet been yielded lived in the generator frame, and
    # GeneratorExit destroyed it. In other words, batch pop made false the invariant _pump
    # declared for itself ("whatever is left in the inbox when the generator ends is relayed
    # by the next pump").
    #
    # Goes through the real runner.AgentRunner — runner.py:144-152 takes this path
    # routinely, and the extra generator layer is the real shape.
    d, runner, cap = _runner(tmp_path, {"questions": True,
                                        "preface_texts": ["문장 1", "문장 2",
                                                          "문장 3"]})
    seen = []
    agen = runner.send_message("hi").__aiter__()
    async for ev in agen:
        seen.append(ev.text)
        break                      # SSE disconnects right after the first frame
    await agen.aclose()
    assert seen == ["문장 1"]
    # The rest must not disappear; it must remain owned.
    assert [e.text for e in d._reader.outbox] == ["문장 1", "문장 2", "문장 3"]

    await _reconnect_gap()
    await runner.pending()         # reconnect: GET /pending returns the question
    later = [ev async for ev in runner.send_answers({"1": "A"})]
    relayed = [e.text for e in later if e.kind == "message"]
    for text in ("문장 2", "문장 3"):
        assert text in relayed, [(e.kind, e.text) for e in later]
    assert [e.kind for e in later][-1] == "done"


@pytest.mark.asyncio
async def test_queued_tool_events_not_yet_yielded_survive_abandonment(tmp_path):
    # The same batch-pop shape existed in drain_queue() too (this one predates round 4).
    # If a tool emitted three during one yield and the consumer vanished after the first, two
    # died. The two loops have the same shape, so they are fixed together with the same rule
    # (pop after delivery).
    d, runner, cap = _runner(tmp_path, {"questions": True})
    seen = []
    agen = runner.send_message("hi").__aiter__()
    async for ev in agen:
        seen.append((ev.kind, ev.path))
        if ev.kind == "questions":
            # Three in one burst — a single MultiEdit, or consecutive report_stage calls.
            for i in (1, 2, 3):
                d._emit(AgentEvent(kind="file_changed", path=f"doc{i}.md"))
        if ev.kind == "file_changed":
            break                  # leave in the middle of the yield sequence
    await agen.aclose()
    assert ("file_changed", "doc1.md") in seen
    assert [e.path for e in d._queue] == ["doc1.md", "doc2.md", "doc3.md"]

    await _reconnect_gap()
    await runner.pending()
    later = [ev async for ev in runner.send_answers({"1": "A"})]
    paths = [e.path for e in later if e.kind == "file_changed"]
    for i in (2, 3):
        assert f"doc{i}.md" in paths, [(e.kind, e.path) for e in later]
    assert [e.kind for e in later][-1] == "done"


@pytest.mark.asyncio
async def test_the_error_path_does_not_strand_queued_events_either(tmp_path):
    # The failure path had the same batch-pop shape. How it is reached: an earlier turn is
    # abandoned in the middle of a relay, leaving items owned in _queue (now possible thanks
    # to round 5's fix), and the next turn dies in query() — there is no _pump yet to relay
    # those items, so the failure path's drain is the only way out. Batch popping here kills
    # the rest the moment the consumer vanishes midway (observed: doc2/doc3 gone).
    from aipds.runner import AgentRunner
    from tests.fakes.fake_sdk import FakeSdkClient

    class _QueryFails(FakeSdkClient):
        async def query(self, text):
            raise RuntimeError("transport died")

    d, ws, _ = _driver(tmp_path, {"text": ["ok"]})
    d._client_factory = lambda session: _QueryFails([])  # type: ignore[assignment]
    runner = AgentRunner(project_id="p1", driver=d, s3=d._s3, local_root=ws,
                         session={"session_id": "s-1"})
    for i in (1, 2, 3):
        d._emit(AgentEvent(kind="file_changed", path=f"doc{i}.md"))

    # Each of the three is asserted separately. Looking only at the _queue contents is
    # vacuous — even deleting the relay call entirely means no "leaving" happens, the loop runs
    # to the end, and since there was no pop, the _queue assertion is automatically true
    # (observed: both mutations gave 37 passed).
    seen = []
    agen = runner.send_message("hi").__aiter__()
    async for ev in agen:
        seen.append((ev.kind, ev.path))
        if ev.kind == "file_changed":
            break                      # leave in the middle of the failure path's relay
    await agen.aclose()
    await _reconnect_gap()

    # (1) The relay actually ran — the consumer received the first item. Without this, the
    #     premise that "leaving happened" collapses and the assertions below become meaningless.
    assert seen[-1] == ("file_changed", "doc1.md"), seen
    # (2) Not a batch pop — the rest remain owned.
    assert [e.path for e in d._queue] == ["doc1.md", "doc2.md", "doc3.md"]
    # (3) error comes after the queue. sse.ts:29 closes the EventSource on error as well as
    #     done, so if error goes out first, the stage/document frames that follow never reach
    #     the client and silently disappear.
    assert "error" not in [k for k, _ in seen], seen

    # Once more with a consumer that does not leave — watch the order to the end.
    d2, ws2, _ = _driver(tmp_path / "second", {"text": ["ok"]})
    d2._client_factory = lambda session: _QueryFails([])  # type: ignore[assignment]
    runner2 = AgentRunner(project_id="p1", driver=d2, s3=d2._s3, local_root=ws2,
                          session={"session_id": "s-1"})
    for i in (1, 2, 3):
        d2._emit(AgentEvent(kind="file_changed", path=f"doc{i}.md"))
    kinds = [ev.kind async for ev in runner2.send_message("hi")]
    assert kinds == ["file_changed"] * 3 + ["error"], kinds


@pytest.mark.asyncio
async def test_the_answers_error_path_relays_the_queue_before_the_error(tmp_path):
    # The failure path of _continue_after_answers has the same shape (the sibling of the
    # _stream side). Here too sse.ts:29 closes the stream on error, so the queue must go out
    # first. Why it is a separate test: the test above is a query() failure and exercises only
    # the _stream side, and applying the same mutation to the sibling path survived.
    d, runner, cap = _runner(tmp_path, {"questions": True,
                                        "turn_continues_after_answer": True})
    ev = [e async for e in runner.send_message("hi")]
    assert any(e.kind == "questions" for e in ev)
    # Tool events that are owned at the moment the answer turn starts.
    for i in (1, 2, 3):
        d._emit(AgentEvent(kind="file_changed", path=f"doc{i}.md"))

    # The exception has to be raised with the queue untouched to exercise this branch.
    # reader.error does not work — it surfaces after the relay has exhausted both queues, so
    # the queue is already empty, and the first version written that way passed via _pump's
    # relay and the mutation survived. translate_into_outbox() runs before the relay on every
    # pass, so blowing up there enters the failure path with the queue intact (a broken
    # message shape).
    def _boom(msg):
        raise RuntimeError("bad message shape")

    d._translate = _boom  # type: ignore[method-assign]
    cap["client"].deliver_late("아무 메시지")

    got = [(e.kind, e.path) async for e in runner.send_answers({"1": "A"})]
    kinds = [k for k, _ in got]
    assert kinds == ["file_changed"] * 3 + ["error"], got
    assert d._queue == []


@pytest.mark.asyncio
async def test_an_already_answered_question_card_is_not_re_shown(tmp_path):
    # The price of pop-after-delivery: an item that was being delivered at the moment of
    # leaving remains owned and goes out again (at-least-once). For questions that is a
    # problem — an already-answered card shows up again, and answering it is rejected with
    # "no pending questions" because there is no future. The questions event of that round
    # exists for the sake of the answers this call carries, so it has been fulfilled; it is
    # not lost.
    d, runner, cap = _runner(tmp_path, {"questions": True,
                                        "turn_continues_after_answer": True,
                                        "preface_texts": ["문장 1"]})
    agen = runner.send_message("hi").__aiter__()
    async for _ in agen:
        break                      # leave before questions has been delivered
    await agen.aclose()
    await _reconnect_gap()
    assert any(e.kind == "questions" for e in d._queue)   # it remained owned

    await runner.pending()
    got = []

    async def answers():
        async for ev in runner.send_answers({"1": "A"}):
            got.append((ev.kind, ev.text))

    task = asyncio.ensure_future(answers())
    for _ in range(8):
        await asyncio.sleep(0)
    cap["client"].finish_turn()
    await asyncio.wait_for(task, 3)

    kinds = [k for k, _ in got]
    assert "questions" not in kinds, got     # do not show the answered card again
    assert runner._pending_interrupt_id is None
    assert kinds[-1] == "done"
    # And yet the body of that turn still comes out.
    assert "문장 1" in [t for _, t in got], got


@pytest.mark.asyncio
async def test_a_new_turn_does_not_lose_messages_to_the_previous_reader(tmp_path):
    # The reader surviving across turns means it must be cleared out when a new turn
    # starts. Reproduction path: question turn → answer turn abandoned midway (SSE
    # disconnect) → the future has already been resolved, so the user's new message goes all
    # the way to query(). If the old reader is still alive at that point, two readers compete
    # on the same anyio stream, and anyio hands the item to the receiver that parked first
    # (= the old reader) — an inbox nobody relays.
    #
    # Observed (without clearing it out): the new turn gets just one sentence, has even the
    # ResultMessage stolen, and never ends — runner.py's loop and the SSE client hang
    # together.
    d, _, cap = _driver(tmp_path, {"questions": True,
                                   "turn_continues_after_answer": True})
    ev1 = [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    iid = json.loads(next(e.payload for e in ev1 if e.kind == "questions"))[
        "interrupt_id"]
    reader1 = d._reader

    # Abandon the answer turn in the middle of the relay.
    agen = d.run_answers(iid, {"1": "A"}, {"session_id": "s-1"}).__aiter__()
    cap["client"].deliver_late("답변 턴 첫 문장")
    async for _ in agen:
        break
    await agen.aclose()
    assert not reader1.task.done()          # the old reader is alive
    assert d._pending_question is None or d._pending_question.done()

    # If a turn the consumer abandoned is still running in the CLI, the new turn is
    # **refused**. If the new message reached query(), `_retire_reader()` would cancel the
    # in-progress turn's reader and push an overlapping query() into the same CLI session —
    # the work the user was waiting on would be thrown away. Refusing also removes the
    # original risk of two readers competing: a second reader is never created.
    events = [ev async for ev in d.run("새 메시지", {"session_id": "s-1"})]
    assert [e.kind for e in events] == ["error"], events
    assert events[0].text == "turn already in progress"
    assert not reader1.task.done()          # the in-progress turn is alive


@pytest.mark.asyncio
async def test_answers_reach_the_sdk_as_the_tool_result(tmp_path):
    # Normal round trip (same process): resolving the waiting future sends the answers to the
    # model as the AskUserQuestion tool result. The number→letter answers must be translated
    # back into SDK labels for the model to know what was selected.
    d, _, captured = _driver(tmp_path, {"questions": True})
    events = [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    payload = json.loads(next(e.payload for e in events if e.kind == "questions"))
    iid = payload["interrupt_id"]
    queries_after_run = list(captured["client"].queries)

    kinds = [e.kind async for e in d.run_answers(iid, {"1": "B"},
                                                {"session_id": "s-1"})]
    assert kinds[-1] == "done"
    # AskingSdkClient records the callback's return value — we look at the updated_input
    # actually returned to the SDK, not the event stream.
    allow = captured["client"].permission_results[0]
    assert allow.updated_input["answers"] == {"다음 단계는?": "종료"}
    # This path does not send a new query() — the turn is still in progress and the CLI was
    # only waiting for the permission response. Sending another query() here would put one
    # more user message into the model and duplicate the turn.
    assert captured["client"].queries == queries_after_run


@pytest.mark.asyncio
async def test_a_second_message_while_a_question_is_parked_re_asks(tmp_path):
    # While a question is parked, the CLI is blocked waiting for the permission response —
    # sending query() in that state gets no response, and the turn polls into the void. The
    # question is shown again without calling the model (the same judgment as StrandsDriver's
    # B1 shortcut).
    d, _, captured = _driver(tmp_path, {"questions": True})
    [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    before = len(captured["client"].queries)

    events = [ev async for ev in d.run("또 다른 말", {"session_id": "s-1"})]
    kinds = [e.kind for e in events]
    assert kinds == ["message", "questions", "done"]
    assert len(captured["client"].queries) == before  # the model was not called


@pytest.mark.asyncio
async def test_pending_reads_from_s3_after_a_restart(tmp_path):
    # A new driver with no in-memory state at all — reproduces a backend restart.
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="i-1",
                       questions={"name": "q", "questions": []},
                       sdk_questions=[{"question": "q", "options": []}],
                       session_id="s-1")
    d, _, _ = _driver(tmp_path, {"text": ["ok"]}, s3=s3)
    payload = await d.pending({"session_id": "s-1"})
    assert payload is not None
    assert json.loads(payload)["interrupt_id"] == "i-1"


@pytest.mark.asyncio
async def test_clears_pending_after_answers_are_submitted(tmp_path):
    # If it remained, a stale form that cannot be answered would appear on refresh.
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="i-1",
                       questions={"name": "q", "questions": []},
                       sdk_questions=[{"question": "질문",
                                       "options": [{"label": "예"}]}],
                       session_id="s-1")
    d, _, _ = _driver(tmp_path, {"text": ["ok"]}, s3=s3)
    [ev async for ev in d.run_answers("i-1", {"1": "A"}, {"session_id": "s-1"})]
    assert PENDING_KEY not in s3.blobs


@pytest.mark.asyncio
async def test_resumes_with_the_answer_as_text_when_the_future_is_gone(tmp_path):
    # Answers after a restart: the awaited Future is gone, so they are delivered through
    # resume + a text turn. The prompt must contain both the question and the chosen label
    # for the model to keep the context.
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="i-1",
                       questions={"name": "q", "questions": []},
                       sdk_questions=[{"question": "다음 단계는?",
                                       "options": [{"label": "진행"},
                                                   {"label": "종료"}]}],
                       session_id="s-1")
    d, _, captured = _driver(tmp_path, {"text": ["ok"]}, s3=s3)
    # Even after a restart, this project's transcript remains on disk — that is what this
    # path takes over. Placing that file where the CLI writes it creates the real state right
    # after a restart.
    from aipds.agent.claude_driver import _sdk_session_id, _transcript_path
    sid, _ = _sdk_session_id({"session_id": "s-1"})
    t = _transcript_path(d._config_dir, d._workspace, sid)
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_text("{}\n", encoding="utf-8")

    [ev async for ev in d.run_answers("i-1", {"1": "A"}, {"session_id": "s-1"})]
    sent = " ".join(captured["client"].queries)
    assert "다음 단계는?" in sent
    assert "진행" in sent
    # It must be the resume path so the transcript continues without a --session-id/--resume
    # conflict. (Half of C1 is that putting resume=True in *unconditionally* is not the
    # answer: --resume without a transcript exits 1 with "No conversation found".
    #  The opposite direction is caught by
    #  test_the_restart_answers_path_does_not_resume_a_missing_transcript.)
    assert captured["session"]["resume"] is True


@pytest.mark.asyncio
async def test_the_answer_record_echoes_the_received_values_not_stored_ones(tmp_path):
    # This proves that the contract's echo_answers check is honest. The contract adapter has
    # to keep the interrupt_id it plants (for round verification) equal to the value the
    # contract passes, so it cannot distinguish "received value vs stored value" — this test
    # makes that distinction.
    #
    # Both values are made different from what is stored: interrupt_id is the same as the
    # stored one (since it must pass round verification) but answers are values unrelated to
    # the stored script, and both the answers keys and values differ from the {"1": "A"} the
    # contract uses. If the driver hardcodes either side, it breaks here.
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="i-round-7",
                       questions={"name": "q", "questions": []},
                       sdk_questions=[{"question": "첫 질문",
                                       "options": [{"label": "가"}, {"label": "나"}]},
                                      {"question": "둘째 질문",
                                       "options": [{"label": "다"}, {"label": "라"}]}],
                       session_id="s-1")
    d, _, cap = _driver(tmp_path, {"text": ["ok"]}, s3=s3)
    submitted = {"2": "B", "1": "A"}
    [ev async for ev in d.run_answers("i-round-7", submitted,
                                      {"session_id": "s-1"})]

    record = json.loads(cap["client"].queries[-1].rsplit("\n", 1)[-1])
    assert record["interrupt_id"] == "i-round-7"
    assert record["answers"] == submitted     # fails if hardcoded
    # The human-readable lines must also be translated back into the stored questions/labels.
    sent = cap["client"].queries[-1]
    assert "둘째 질문 → 라" in sent
    assert "첫 질문 → 가" in sent


@pytest.mark.asyncio
async def test_answers_without_any_pending_question_are_refused(tmp_path):
    # The contract string. With neither a pending record nor a future, there is no context to revive.
    d, _, _ = _driver(tmp_path, {"text": ["ok"]})
    events = [ev async for ev in d.run_answers("i-gone", {"1": "A"},
                                              {"session_id": "s-1"})]
    assert [e.kind for e in events] == ["error"]
    assert events[0].text == "no pending questions"


@pytest.mark.asyncio
async def test_a_stale_interrupt_id_does_not_answer_the_live_question(tmp_path):
    # An old tab sending an answer for a past round. Resolving the live future with it would
    # give the model the answer to a different question.
    d, _, _ = _driver(tmp_path, {"questions": True})
    [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    events = [ev async for ev in d.run_answers("i-stale", {"1": "A"},
                                              {"session_id": "s-1"})]
    assert [e.kind for e in events] == ["error"]
    assert events[0].text == "no pending questions"
    assert await d.pending({"session_id": "s-1"}) is not None  # the question is alive


@pytest.mark.asyncio
async def test_a_pending_s3_failure_does_not_kill_the_turn(tmp_path):
    # Persisting pending is a restoration convenience. Losing the in-progress question because of it is the bigger loss.
    class _Broken(FakeS3Store):
        async def put(self, key, content):
            raise RuntimeError("s3 down")

    d, _, _ = _driver(tmp_path, {"questions": True}, s3=_Broken())
    events = [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    kinds = [e.kind for e in events]
    assert "questions" in kinds
    assert "error" not in kinds


@pytest.mark.asyncio
async def test_a_missing_rules_dir_is_reported_as_a_turn_failure(tmp_path):
    # place_rules raises FileNotFoundError when the rules are missing — proceeding silently
    # would have the agent run without knowing the workflow. It is downgraded to the contract
    # string.
    d, _, _ = _driver(tmp_path, {"text": ["ok"]})
    d._rules_dir = str(tmp_path / "does-not-exist")
    events = [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    assert [e.kind for e in events] == ["error"]
    assert events[0].text == "agent turn failed"


@pytest.mark.asyncio
async def test_places_the_rules_on_the_restart_answers_path_too(tmp_path):
    # This is the only path where the workspace is certainly empty: having no future means the
    # backend was redeployed, and runner.py:36 restores only aiplc-docs/, prototype/, and
    # uploads/ — it never restores the rules. The first action right after a restart must not
    # run without the workflow.
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="i-1",
                       questions={"name": "q", "questions": []},
                       sdk_questions=[{"question": "Q",
                                       "options": [{"label": "진행"}]}],
                       session_id="s-1")
    d, ws, _ = _driver(tmp_path, {"text": ["ok"]}, s3=s3)
    assert not (ws / "CLAUDE.md").exists()  # starts cold
    [ev async for ev in d.run_answers("i-1", {"1": "A"}, {"session_id": "s-1"})]
    # CLAUDE.md is now an assembly: the language directive, then the workflow. The directive
    # comes from code (agent/workspace_rules.LANGUAGE_DIRECTIVES), not from a fixture.
    text = (ws / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.index("언어 규약") < text.index("WORKFLOW")


@pytest.mark.asyncio
async def test_a_stale_interrupt_id_is_refused_on_the_restart_path_too(tmp_path):
    # An old tab sending an answer for a past round after a restart. Without the guard, the
    # stored current question would be answered with the wrong answer, and on the way out the
    # real pending record would be deleted too — the live form would become unanswerable.
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="i-CURRENT",
                       questions={"name": "q", "questions": []},
                       sdk_questions=[{"question": "NEW question",
                                       "options": [{"label": "진행"}]}],
                       session_id="s-1")
    d, _, cap = _driver(tmp_path, {"text": ["ok"]}, s3=s3)
    events = [ev async for ev in d.run_answers("i-STALE-FROM-OLD-TAB", {"1": "A"},
                                              {"session_id": "s-1"})]
    assert [e.kind for e in events] == ["error"]
    assert events[0].text == "no pending questions"
    assert "client" not in cap                # the model was not called
    assert PENDING_KEY in s3.blobs            # the real question is alive


@pytest.mark.asyncio
async def test_an_abandoned_turn_frees_the_slot_immediately(tmp_path):
    # runner.py:144-152 takes this path routinely (SSE disconnect, proxy timeout, the user
    # leaving). runner clears its own _turn_active synchronously in the same finally, so if
    # our side takes extra ticks, the retry from a browser that reconnected on the very next
    # tick bounces with "turn already in progress".
    d, _, _ = _driver(tmp_path, {"questions": True})
    agen = d.run("hi", {"session_id": "s-1"}).__aiter__()
    await agen.__anext__()
    await agen.aclose()
    assert d._turn_active is False   # right after aclose(), with no extra tick

    # The retry is actually accepted.
    kinds = [e.kind async for e in d.run("retry", {"session_id": "s-1"})]
    assert "error" not in kinds


@pytest.mark.asyncio
async def test_a_concurrent_turn_is_still_rejected(tmp_path):
    # Even with slot ownership moved up to run/run_answers, the contract string is preserved.
    d, _, _ = _driver(tmp_path, {"questions": True})
    agen = d.run("hi", {"session_id": "s-1"}).__aiter__()
    await agen.__anext__()
    try:
        events = [ev async for ev in d.run("동시", {"session_id": "s-1"})]
        assert [e.kind for e in events] == ["error"]
        assert events[0].text == "turn already in progress"
    finally:
        await agen.aclose()


@pytest.mark.asyncio
async def test_disconnect_tears_down_the_subprocess_and_clears_pending(tmp_path):
    # runner.stop() only does rmtree — without disconnect, a claude subprocess leaks for every
    # deleted project. The parked question must be cleaned up too, so pending() does not
    # advertise a question that can never be answered.
    d, _, cap = _driver(tmp_path, {"questions": True})
    [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    assert await d.pending({"session_id": "s-1"}) is not None

    await d.disconnect()
    assert cap["client"].disconnect_calls == 1
    assert d._client is None
    assert d._pending_payload is None
    await d.disconnect()  # idempotent


@pytest.mark.asyncio
async def test_disconnect_clears_the_s3_pending_record_too(tmp_path):
    # Task 8 carry-forward defect: disconnect() only cleared the in-memory
    # _pending_payload. pending() checks that FIRST but falls back to the S3
    # record (load_pending) when it's None -- so leaving the S3 record behind
    # made pending() advertise the dead question anyway, just via the other
    # path. The subprocess is gone with disconnect(), so no future will ever
    # resolve it.
    d, _, cap = _driver(tmp_path, {"questions": True})
    [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    assert await d.pending({"session_id": "s-1"}) is not None  # sanity: live

    await d.disconnect()

    assert await d.pending({"session_id": "s-1"}) is None


@pytest.mark.asyncio
async def test_a_fresh_turn_after_disconnect_does_not_re_yield_a_dead_question(
        tmp_path):
    # Task 8 carry-forward defect: if a turn was abandoned before its
    # `questions` event was delivered, that event sits UNPOPPED at the head of
    # self._queue (by design -- see `_pump`'s ownership rule). disconnect()
    # tore down the subprocess and future but never dropped it, so the next
    # turn's _pump still relayed it -- a card for a question nobody can ever
    # answer (its future died with the subprocess).
    d, _, cap = _driver(tmp_path, {"questions": True})
    agen = d.run("hi", {"session_id": "s-1"}).__aiter__()
    assert (await agen.__anext__()).kind == "message"
    assert (await agen.__anext__()).kind == "questions"
    await agen.aclose()  # abandoned before the questions event is popped
    assert any(e.kind == "questions" for e in d._queue)  # sanity: still owned

    await d.disconnect()
    assert not any(e.kind == "questions" for e in d._queue)  # dead card dropped

    # A fresh turn (new subprocess, no question in its own script) must not
    # surface the old one either.
    d._client_factory = lambda session: sdk_client_for(
        {"text": ["ok"]}, d._on_can_use_tool)
    kinds = [e.kind async for e in d.run("new message", {"session_id": "s-1"})]
    assert "questions" not in kinds, kinds
    assert kinds == ["message", "done"], kinds


@pytest.mark.asyncio
async def test_uses_the_discovery_config_dir_not_the_prototype_one(tmp_path):
    # If shared, Discovery would run with the shadcn-design skill enabled.
    d, _, captured = _driver(tmp_path, {"text": ["ok"]})
    [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    # Check the config dir in the options passed to client_factory.
    assert str(tmp_path / "cfg") == d._config_dir


# ---- SDK session id: the CLI accepts only UUIDs ----
# Observed: `claude --session-id=ws-m -p hi` → "Error: Invalid session ID. Must be
# a valid UUID.", exit 1. app.py:255 uses project_id as is for session_id, and that is free
# input (CreateProject in routes/projects.py does not validate it), so a project like
# "ws-m" dies in connect() on every turn.

def test_a_non_uuid_session_id_becomes_a_stable_uuid():
    import uuid

    from aipds.agent.claude_driver import _sdk_session_id

    sid, resume = _sdk_session_id({"session_id": "ws-m", "resume": True})
    uuid.UUID(sid)                       # a form the CLI accepts
    assert sid != "ws-m"
    assert resume is True
    # It must be the same after a restart for --resume to find the transcript.
    again, _ = _sdk_session_id({"session_id": "ws-m", "resume": True})
    assert again == sid
    # A different project gets a different id.
    other, _ = _sdk_session_id({"session_id": "pilot2", "resume": True})
    assert other != sid


def test_an_already_uuid_session_id_is_passed_through():
    import uuid

    from aipds.agent.claude_driver import _sdk_session_id

    given = str(uuid.uuid4())
    sid, resume = _sdk_session_id({"session_id": given, "resume": True})
    assert sid == given          # do not overwrite a caller that does it right
    assert resume is True


def test_a_missing_session_id_does_not_claim_to_resume():
    import uuid

    from aipds.agent.claude_driver import _sdk_session_id

    sid, resume = _sdk_session_id({"resume": True})
    uuid.UUID(sid)
    assert resume is False       # there is no transcript to take over


# ---- C1: the CLI rejects the same session id used by two processes ----
#
# Observed (bundled binary 2.1.220):
#   claude --session-id=<uuid already used> -p hi  -> exit 1, "Session ID ... is
#                                                     already in use."
#   claude --resume=<nonexistent uuid>      -p hi  -> exit 1, "No conversation found"
# The two errors are each other's complement, so neither flag can be used unconditionally.
# The only basis for the decision is the transcript file on disk (once that file was moved,
# the --session-id that had just been rejected succeeded again), so the driver checks that
# file directly.
#
# Why the tests below use SessionIdCheckingSdkClient: FakeSdkClient.connect() is a no-op,
# so this whole defect was invisible to 624 green tests. A fake that cannot reject a
# duplicate session id proves nothing.


def _checking_driver(tmp_path, script=None, config_dir=None, workspace=None):
    """A driver wired to SessionIdCheckingSdkClient + the list of clients its factory made.

    Why config_dir/workspace are parameters: a restart is "a new driver instance, the same
    config dir + the same workspace", so the two drivers must see the same disk state.
    """
    from tests.fakes.fake_sdk_asking import SessionIdCheckingSdkClient

    rules = tmp_path / "rules" / "aws-aiplc-rules"
    if not rules.exists():
        rules.mkdir(parents=True)
        (rules / "core-workflow.md").write_text("WORKFLOW", encoding="utf-8")
        # The fixture does not create the language directive — it comes from the package
        # (agent/workspace_rules.LANGUAGE_DIRECTIVES), not from `rules_dir`.
    ws = workspace or (tmp_path / "ws")
    ws.mkdir(parents=True, exist_ok=True)
    cfg = config_dir or (tmp_path / "cfg")
    cfg.mkdir(parents=True, exist_ok=True)

    made: list = []
    d = ClaudeDriver(workspace=str(ws), rules_dir=str(tmp_path / "rules"),
                     config_dir=str(cfg), s3=FakeS3Store(),
                     client_factory=lambda session: None)

    def factory(session):
        c = SessionIdCheckingSdkClient(session, str(cfg), str(ws),
                                       script=script)
        made.append(c)
        return c

    d._client_factory = factory  # type: ignore[assignment]
    return d, made, cfg, ws


@pytest.mark.asyncio
async def test_an_ordinary_turn_survives_a_backend_restart(tmp_path):
    # C1 itself. Process 1 runs a turn and leaves a transcript. When the backend restarts
    # (= a new driver instance, the same config dir/workspace), an ordinary message turn comes
    # in with the resume=False default, but the session id is derived from the project id via
    # uuid5 and is *stable*, so --session-id collides with that transcript.
    # Observed: every turn in processes 2/3 dies with "agent turn failed", and since the
    # transcript file is permanent, it never heals on its own.
    session = {"session_id": "acme"}          # free-form project id (not a uuid)
    d1, made1, cfg, ws = _checking_driver(tmp_path)
    first = [e.kind async for e in d1.run("hi", session)]
    assert first == ["done"], first
    assert made1[0].connected

    # Restart: the same project, the same disk, a new driver.
    d2, made2, _, _ = _checking_driver(tmp_path, config_dir=cfg, workspace=ws)
    second = [e.kind async for e in d2.run("다시 안녕", session)]
    assert second == ["done"], second        # used to be ["error"]
    # And the evidence that it took over: it attached via --resume.
    assert made2[0]._session["resume"] is True
    # A third process is the same (not passing once by chance).
    d3, made3, _, _ = _checking_driver(tmp_path, config_dir=cfg, workspace=ws)
    assert [e.kind async for e in d3.run("또", session)] == ["done"]
    assert made3[0]._session["resume"] is True


@pytest.mark.asyncio
async def test_a_deleted_and_recreated_project_still_gets_a_working_turn(tmp_path):
    # C1's second trigger — no restart needed at all. The last item of checklist §7 is exactly
    # this path: DELETE /projects/acme -> runner.stop() -> disconnect(), and then recreating it
    # with the same project_id in the same process.
    # Observed (before the fix): turn 1 fine, turn 2 "agent turn failed".
    session = {"session_id": "acme"}
    d1, _, cfg, ws = _checking_driver(tmp_path)
    assert [e.kind async for e in d1.run("hi", session)] == ["done"]
    await d1.disconnect()                     # delete the project

    d2, made2, _, _ = _checking_driver(tmp_path, config_dir=cfg, workspace=ws)
    assert [e.kind async for e in d2.run("다시", session)] == ["done"]
    assert made2[0]._session["resume"] is True


@pytest.mark.asyncio
async def test_the_restart_answers_path_does_not_resume_a_missing_transcript(tmp_path):
    # The opposite direction. _resume_with_answers requests resume=True, but when there is
    # no transcript (a redeploy that also recycled the config dir — instance replacement,
    # /opt reset), --resume exits 1 with "No conversation found". Trusting the caller's intent
    # as is kills the first answer after a restart.
    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="i-1",
                       questions={"name": "q", "questions": []},
                       sdk_questions=[{"question": "다음 단계는?",
                                       "options": [{"label": "진행"}]}],
                       session_id="acme")
    d, made, cfg, ws = _checking_driver(tmp_path)
    d._s3 = s3
    events = [e.kind async for e in d.run_answers("i-1", {"1": "A"},
                                                  {"session_id": "acme"})]
    assert events[-1] == "done", events
    assert "error" not in events, events
    # There was no transcript, so it should have fallen back to fresh.
    assert made[0]._session["resume"] is False


@pytest.mark.asyncio
async def test_the_answers_path_resumes_when_the_transcript_is_there(tmp_path):
    # And when the transcript is there, it must take it over — otherwise the answers after a
    # restart go into a new conversation with no context (making the model understanding that
    # §6 verifies meaningless), and it dies from the --session-id collision.
    session = {"session_id": "acme"}
    d1, _, cfg, ws = _checking_driver(tmp_path)
    assert [e.kind async for e in d1.run("hi", session)] == ["done"]

    s3 = FakeS3Store()
    await save_pending(s3, interrupt_id="i-1",
                       questions={"name": "q", "questions": []},
                       sdk_questions=[{"question": "다음 단계는?",
                                       "options": [{"label": "진행"}]}],
                       session_id="acme")
    d2, made2, _, _ = _checking_driver(tmp_path, config_dir=cfg, workspace=ws)
    d2._s3 = s3
    events = [e.kind async for e in d2.run_answers("i-1", {"1": "A"}, session)]
    assert events[-1] == "done", events
    assert made2[0]._session["resume"] is True


def test_the_transcript_path_matches_the_cli_layout(tmp_path):
    # The path rule is observed, not guessed (bundle 2.1.220). Checked with eight cwds, and
    # every directory name the CLI created matched
    # re.sub(r"[^A-Za-z0-9-]", "-", cwd):
    #   /tmp/pf-probe/acme_1.2-x -> -tmp-pf-probe-acme-1-2-x
    #   /tmp/pf-e2/Acme Corp     -> -tmp-pf-e2-Acme-Corp
    #   /tmp/pf-k/한글프로젝트     -> -tmp-pf-k-------
    from aipds.agent.claude_driver import _transcript_path

    p = _transcript_path("/opt/aipds/discovery-config",
                         "/tmp/aipds-workspaces/acme_1.2-x",
                         "bde34f1e-bdb0-5f78-8ca2-07822c3609a0")
    assert p.parent.name == "-tmp-aipds-workspaces-acme-1-2-x"
    assert p.name == "bde34f1e-bdb0-5f78-8ca2-07822c3609a0.jsonl"
    assert p.parent.parent.name == "projects"

    # A Korean project id must also look at the same place as the CLI (Discovery's project id
    # is free input, so this is a common case). Below is exactly the directory name the real
    # CLI created — `/tmp/pf-k/한글프로젝트` (6 characters) -> `-tmp-pf-k-------`. The
    # encoding is irreversible, so Korean characters all fold into "-".
    k = _transcript_path("/cfg", "/tmp/pf-k/한글프로젝트",
                         "aaaaaaaa-1111-4222-8333-444455556666")
    assert k.parent.name == "-tmp-pf-k-------"

    # When cwd is a symbolic link, the CLI encodes the real path (observed: the transcript was
    # created in the directory of the target path, not the link name).
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    assert _transcript_path("/cfg", str(link), "x").parent.name == \
        _transcript_path("/cfg", str(real), "x").parent.name


def test_the_transcript_probe_reads_the_file_the_cli_writes(tmp_path):
    # Whether _transcript_exists only checks "does that file exist". That is exactly the CLI's
    # "already in use" check — once the file was moved, the same --session-id succeeded again.
    from aipds.agent.claude_driver import (
        _transcript_exists, _transcript_path,
    )

    cfg = tmp_path / "cfg"
    ws = tmp_path / "ws"
    ws.mkdir()
    sid = "bde34f1e-bdb0-5f78-8ca2-07822c3609a0"
    assert _transcript_exists(str(cfg), str(ws), sid) is False
    p = _transcript_path(str(cfg), str(ws), sid)
    p.parent.mkdir(parents=True)
    p.write_text("{}\n", encoding="utf-8")
    assert _transcript_exists(str(cfg), str(ws), sid) is True
    # Other session ids are unaffected.
    assert _transcript_exists(str(cfg), str(ws),
                              "cccccccc-1111-4222-8333-444455556666") is False
    # A directory is not a file — the reason it has to be is_file().
    p.unlink()
    p.mkdir()
    assert _transcript_exists(str(cfg), str(ws), sid) is False


# ---- I3: a failed connect() must not permanently poison the cache ----


@pytest.mark.asyncio
async def test_a_failed_connect_does_not_poison_the_client_cache(tmp_path):
    # Observed (before the fix): _client was assigned *before* the connect() await, so after
    # one failure a broken client stayed in the cache, the next three turns were all "agent
    # turn failed" (CLIConnectionError: Not connected), and the factory was called exactly
    # once. This is the mechanism that amplified C1 from "one failure" into "a dead project".
    from tests.fakes.fake_sdk import FakeSdkClient

    class _FailsOnce(FakeSdkClient):
        fail = True

        async def connect(self):
            if type(self).fail:
                raise RuntimeError("connect boom")
            await super().connect()

    made: list = []
    rules = tmp_path / "rules" / "aws-aiplc-rules"
    rules.mkdir(parents=True)
    (rules / "core-workflow.md").write_text("WORKFLOW", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    d = ClaudeDriver(workspace=str(ws), rules_dir=str(tmp_path / "rules"),
                     config_dir=str(tmp_path / "cfg"), s3=FakeS3Store(),
                     client_factory=lambda s: None)

    def factory(session):
        c = _FailsOnce([__import__("tests.fakes.fake_sdk", fromlist=["x"])
                        .ResultMessage()])
        made.append(c)
        return c

    d._client_factory = factory  # type: ignore[assignment]

    first = [e.kind async for e in d.run("hi", {"session_id": "acme"})]
    assert first == ["error"]
    # The cache must be empty so the next turn creates a new client.
    assert d._client is None

    _FailsOnce.fail = False
    second = [e.kind async for e in d.run("다시", {"session_id": "acme"})]
    assert second == ["done"], second        # used to be ["error"]
    assert len(made) == 2, made              # a new client was really created
    assert made[1].connected


# ---- I2: whether pending() actually makes "answering after a refresh" possible ----


@pytest.mark.asyncio
async def test_a_refresh_mid_question_can_still_submit_the_answer(tmp_path):
    # The shared contract (driver_contract.py) only requires "when a question is up, pending()
    # returns that round" — because the two drivers keep that value in different places. Here,
    # on the ClaudeDriver side, *what that value makes possible* is run all the way through a
    # real AgentRunner: GET /pending -> POST /answers.
    #
    # Failure shape: if pending() is None, runner._pending_interrupt_id is not planted, and
    # send_answers refuses with "no pending questions" without even calling the driver
    # (runner.py:158-160). That is, a user who refreshed mid-question has no way to answer,
    # ever — with all 40 tests green.
    d, runner, cap = _runner(tmp_path, {"questions": True})
    first = [ev.kind async for ev in runner.send_message("hi")]
    assert "questions" in first and first[-1] == "done", first

    # Refresh: the frontend calls GET /pending before the new SSE connection.
    runner._pending_interrupt_id = None      # new page load = no in-memory state
    payload = await runner.pending()
    assert payload is not None, "새로고침 시 질문 폼을 복원할 수 없다"
    assert runner._pending_interrupt_id is not None, \
        "pending()이 라운드를 심지 않아 send_answers가 거절할 것이다"

    later = [ev async for ev in runner.send_answers({"1": "A"})]
    kinds = [e.kind for e in later]
    assert kinds[-1] == "done", kinds
    assert not any(e.kind == "error" and e.text == "no pending questions"
                   for e in later), [(e.kind, e.text) for e in later]


# ---- Transcript mirroring: a turn parked on a question must also remain in S3 ----

def _captured_options(tmp_path, monkeypatch, session, **driver_kw):
    """Capture the ClaudeAgentOptions assembled by the real _default_client_factory.

    Every other test in this file injects client_factory, so none of them takes this path —
    they all pass even if the wiring is missing. That is exactly how it was missed:
    session_store was attached, yet the workspace history was empty, and with no test checking
    that combination, the cause only surfaced in production logs.
    """
    from aipds.agent.claude_driver import _default_client_factory

    captured = {}

    class FakeClient:
        def __init__(self, options=None):
            captured["options"] = options

    import claude_agent_sdk
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", FakeClient)

    driver = ClaudeDriver(workspace=str(tmp_path), rules_dir=str(tmp_path),
                          config_dir=str(tmp_path / "cfg"), s3=FakeS3Store(), **driver_kw)
    _default_client_factory(driver)(session)
    return captured["options"]


def test_the_cli_debug_log_is_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("AIPDS_SDK_CLI_DEBUG", raising=False)
    options = _captured_options(tmp_path, monkeypatch, {"session_id": "p1", "resume": False})
    assert "debug-file" not in (options.extra_args or {})


def test_the_cli_debug_switch_writes_into_the_projects_config_dir(tmp_path, monkeypatch):
    """That tree is where the wrapper's sandbox allows writes (cli_settings.cli_debug_args)."""
    monkeypatch.setenv("AIPDS_SDK_CLI_DEBUG", "1")
    options = _captured_options(tmp_path, monkeypatch, {"session_id": "p1", "resume": False})
    path = options.extra_args["debug-file"]
    assert path.startswith(str(tmp_path / "cfg") + "/cli-debug-")
    assert path.endswith(".log")


def test_the_projects_effort_reaches_the_cli(tmp_path, monkeypatch):
    """The effort chosen together with the model in the catalog reaches the SDK option (`--effort`)."""
    options = _captured_options(tmp_path, monkeypatch,
                                {"session_id": "p1", "resume": False}, effort="high")
    assert options.effort == "high"


def test_no_effort_leaves_the_cli_default(tmp_path, monkeypatch):
    """A project without effort (predating this field) passes no flag — it runs with the CLI default."""
    options = _captured_options(tmp_path, monkeypatch,
                                {"session_id": "p1", "resume": False})
    assert options.effort is None


def test_transcript_mirroring_flushes_in_turn_batches(tmp_path, monkeypatch):
    """Flush at explicit AI-PDS turn boundaries instead of an S3 PUT per frame."""
    options = _captured_options(tmp_path, monkeypatch,
                                {"session_id": "p1", "resume": False})
    assert options.session_store is not None, "미러링 자체가 꺼져 있다"
    assert options.session_store_flush == "batched"


async def test_parking_on_a_question_flushes_the_transcript(tmp_path):
    """When a turn is closed out by a question, the mirror batcher must be flushed directly.

    Batched mode cannot rely only on the SDK's result/close, so AI-PDS must flush directly
    before the question terminal.
    """
    flushed = []

    class FakeBatcher:
        async def flush(self):
            flushed.append(True)

    d, _, captured = _driver(tmp_path, {"questions": True})

    kinds = []
    async for ev in d.run("hi", {"session_id": "p1"}):
        # Planted where the real SDK keeps the batcher, after the client has been created
        # (the fake the factory calls has no _query).
        client = captured.get("client")
        if client is not None and not hasattr(client, "_query"):
            client._query = type(
                "Q", (), {"_transcript_mirror_batcher": FakeBatcher()})()
        kinds.append(ev.kind)
    assert "questions" in kinds and kinds[-1] == "done", kinds
    assert flushed, "질문에서 파킹된 턴의 트랜스크립트가 flush되지 않았다"


async def test_a_mirror_error_is_logged(tmp_path, caplog):
    """When the SDK reports a mirroring failure, it must be logged.

    A failed batch is not retried (at-most-once) — this system message is the only signal
    that reaches the consumer, and we were throwing it away entirely. So when S3 had no
    transcript, there was no way to tell "the write failed" from "the write was never
    attempted". It is not shown to the user: history durability is auxiliary data, and there
    is no reason to break the in-progress turn.

    **The message is built with the SDK's real parser.** Previously this test passed by
    overwriting the fake's `__name__` with `"SystemMessage"`, and that is why this branch
    stayed green while **dead**: the parser always produces a `MirrorErrorMessage` for
    `mirror_error` (`_internal/message_parser.py`), and that class name is not
    `"SystemMessage"`. If a fake lies about its class name, code that branches on the name
    cannot be verified — so the parser output is fed in as is.
    """
    import logging

    from claude_agent_sdk._internal.message_parser import parse_message

    msg = parse_message({"type": "system", "subtype": "mirror_error",
                         "error": "S3 PutObject denied"})

    d, _, _ = _driver(tmp_path, {})
    with caplog.at_level(logging.WARNING, logger="aipds.agent"):
        events = d._translate(msg)

    assert events == [], "미러링 실패를 사용자 이벤트로 만들면 안 된다"
    assert "mirror" in caplog.text.lower()
    assert "S3 PutObject denied" in caplog.text


# ---- Interrupting a turn ----

async def test_interrupt_clears_the_pending_question_from_s3(tmp_path):
    """An interrupt must also delete the pending record in S3.

    Discovery's pending lives both in memory and in S3 (agent/pending_store.py). Clearing only
    the in-memory one makes `GET /pending` restore a question that cannot be answered — the
    user fills in the form and submits it, and nothing happens, because that future was
    discarded along with the interrupt. This is why the prototype builder does the same cleanup
    (interrupt in proto/builder.py), and Discovery has one more durable copy.
    """
    s3 = FakeS3Store()
    d, _, _ = _driver(tmp_path, {"questions": True}, s3=s3)
    kinds = [ev.kind async for ev in d.run("hi", {"session_id": "s-1"})]
    assert "questions" in kinds, kinds
    assert PENDING_KEY in s3.blobs, "전제: 질문이 S3에 저장돼 있다"

    await d.interrupt()

    assert PENDING_KEY not in s3.blobs
    assert d._pending_payload is None
    assert d._pending_iid is None


async def test_interrupt_without_a_live_turn_is_a_no_op(tmp_path):
    """It must be idempotent. An interrupt request for a turn that has already ended is not an
    error, and the route calls this method based only on whether a session exists."""
    d, _, _ = _driver(tmp_path, {})
    await d.interrupt()   # no turn has run
    await d.interrupt()   # calling it twice is the same


async def test_interrupt_records_that_the_turn_was_stopped(tmp_path):
    """The fact of the interrupt must flow as an event so it remains on screen and in the transcript.

    Without the marker, someone reading the scrollback later cannot tell why the agent did not
    finish what it was saying.
    """
    d, _, _ = _driver(tmp_path, {"questions": True})
    [ev async for ev in d.run("hi", {"session_id": "s-1"})]

    await d.interrupt()

    from aipds.agent.claude_driver import INTERRUPTED_MARKER
    assert any(e.kind == "status" and e.text == INTERRUPTED_MARKER
               for e in d._queue), d._queue


def test_interrupt_marker_is_language_neutral():
    """The frontend compares against this string to set interrupted
    (frontend/lib/useWorkspaceStream.ts). If it were Korean, translating the UI would leave the
    frontend unable to recognize the interrupt — the single '중단됨' (stopped) line would not
    appear on screen, and the turn would look as if it had succeeded.

    proto/builder.py already uses the same value — if the two drivers diverge, the frontend
    behaves differently depending on the path."""
    from aipds.agent.claude_driver import INTERRUPTED_MARKER
    assert INTERRUPTED_MARKER == "interrupted"
    # It is not human-readable text, so it must contain no non-ASCII characters.
    assert INTERRUPTED_MARKER.isascii()


def test_driver_places_the_project_language_directive(tmp_path):
    """The driver passes the project language to place_rules.

    If this wiring is missing, every project runs with the Korean directive, and a user who
    chose English gets Korean documents in an English UI — with no error.
    """
    from aipds.agent.claude_driver import ClaudeDriver
    seen = {}

    def fake_place_rules(workspace, rules_dir, language="ko"):
        seen["language"] = language

    import aipds.agent.claude_driver as mod
    original = mod.place_rules
    mod.place_rules = fake_place_rules
    try:
        d = ClaudeDriver(workspace=str(tmp_path), rules_dir=str(tmp_path),
                         config_dir=str(tmp_path), s3=None,
                         language="en", session_store=None)
        assert d._place_rules() is True
        assert seen["language"] == "en"
    finally:
        mod.place_rules = original


def test_driver_defaults_to_korean(tmp_path):
    from aipds.agent.claude_driver import ClaudeDriver
    d = ClaudeDriver(workspace=str(tmp_path), rules_dir=str(tmp_path),
                     config_dir=str(tmp_path), s3=None, session_store=None)
    assert d._language == "ko"


# ---- Discovery write-scope gate (PreToolUse) ----
# 2026-08-16: the agent went ahead and created `prototype/index.html` in the workspace.
# The rule existed only as prose in discovery-config/CLAUDE.md, and what that prose forbade
# was build *commands*, so a single self-contained HTML file satisfied every clause and got
# through. The decision table is covered by tests/test_discovery_guard.py; here the **wiring**
# is pinned down.


def test_the_gate_is_wired_and_excludes_askuserquestion(tmp_path, monkeypatch):
    """**This test protects the question feature.**

    From the can_use_tool description in SDK types.py: if a PreToolUse hook returns *allow*,
    can_use_tool is skipped too. Our AskUserQuestion interception lives in that callback, so
    the moment AskUserQuestion gets into the matcher, the whole question round trip dies.
    """
    options = _captured_options(tmp_path, monkeypatch,
                                {"session_id": "p1", "resume": False})
    pre = (options.hooks or {}).get("PreToolUse")
    assert pre, "PreToolUse 훅이 없으면 bypassPermissions에서 아무것도 막지 못한다"
    matchers = [m.matcher for m in pre]
    assert any("Write" in (m or "") and "Bash" in (m or "") for m in matchers), matchers
    assert not any("AskUserQuestion" in (m or "") for m in matchers), matchers
    # can_use_tool must stay alive — it is the only path by which a question becomes an SSE event.
    assert options.can_use_tool is not None


def _pre(driver, tool_name, tool_input):
    return driver._on_pre_tool_use(
        {"tool_name": tool_name, "tool_input": tool_input}, "t1", None)


async def test_the_gate_denies_the_html_that_caused_it(tmp_path):
    d, _, _ = _driver(tmp_path, {"text": ["ok"]})
    out = await _pre(d, "Write", {"file_path": "prototype/index.html"})
    decision = out["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert decision["hookEventName"] == "PreToolUse"
    # The model reads the denial reason — it must say what was caught and where to write
    # instead, so the model does not fall into a loop of retrying with only the path changed.
    assert "prototype/index.html" in decision["permissionDecisionReason"]
    # It offers an alternative but **does not pin down the layout** — for Path B the slug path
    # is right, for Path A.1 the singular `prototype/` (proto/layout.py). Pinning one would be
    # a wrong instruction for the agent on the other path.
    assert "aiplc-docs/discovery/" in decision["permissionDecisionReason"]
    assert "PROTOTYPE-" not in decision["permissionDecisionReason"]


async def test_the_gate_denies_writing_the_survey_aggregate(tmp_path):
    """Web-owned files are blocked even inside aiplc-docs/. If the denial reason were "write
    inside aiplc-docs/", the model would retry with the same path — so it has to point to where
    the Step 6 synthesis should go."""
    d, _, _ = _driver(tmp_path, {"text": ["ok"]})
    rel = "aiplc-docs/discovery/prototype/survey-aggregate.md"
    for tool in ("Write", "Edit"):
        out = await _pre(d, tool, {"file_path": rel})
        decision = out["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny", tool
        assert rel in decision["permissionDecisionReason"]
        assert "validation-results.md" in decision["permissionDecisionReason"]
    d._language = "en"
    reason = (await _pre(d, "Write", {"file_path": rel})
              )["hookSpecificOutput"]["permissionDecisionReason"]
    assert not {c for c in reason if "가" <= c <= "힣"}, reason
    assert await _pre(d, "Write", {"file_path":
        "aiplc-docs/discovery/prototype/validation-results.md"}) == {}


async def test_the_gate_denies_overwriting_the_audit_log(tmp_path):
    d, ws, _ = _driver(tmp_path, {"text": ["ok"]})
    (ws / "aiplc-docs").mkdir()
    (ws / "aiplc-docs" / "audit.md").write_text("# AI-PLC Audit Log\n", encoding="utf-8")
    out = await _pre(d, "Write", {"file_path": str(ws / "aiplc-docs" / "audit.md")})
    decision = out["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "Edit" in decision["permissionDecisionReason"]
    assert await _pre(d, "Edit", {"file_path": str(ws / "aiplc-docs" / "audit.md")}) == {}


async def test_the_gate_denies_build_and_serve_commands(tmp_path):
    d, _, _ = _driver(tmp_path, {"text": ["ok"]})
    for command in ("npm run dev", "cd prototype && python3 -m http.server 8000"):
        out = await _pre(d, "Bash", {"command": command})
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny", command


async def test_the_gate_passes_by_returning_an_empty_dict(tmp_path):
    """A pass must be an empty dict — returning "allow" skips can_use_tool and kills the
    AskUserQuestion interception in that callback (the same rationale as the test above)."""
    d, _, _ = _driver(tmp_path, {"text": ["ok"]})
    docs = await _pre(d, "Write",
                      {"file_path": "aiplc-docs/discovery/discovery-document.md"})
    assert docs == {}
    spec = await _pre(d, "Write", {"file_path":
        "aiplc-docs/discovery/prototypes/maint/PROTOTYPE-maint.md"})
    assert spec == {}, "슬러그 산출물이 막히면 문제가 뒤바뀐다"
    assert await _pre(d, "Bash", {"command": "ls aiplc-docs"}) == {}
    # Tools outside the matcher pass silently too.
    assert await _pre(d, "Read", {"file_path": "/etc/hosts"}) == {}


async def test_the_gate_speaks_the_project_language(tmp_path):
    """The denial reason is text that goes into the model context — it must follow the project
    language, per the convention in the agent/prompts.py header."""
    d, _, _ = _driver(tmp_path, {"text": ["ok"]})
    d._language = "en"
    reason = (await _pre(d, "Write", {"file_path": "prototype/index.html"})
              )["hookSpecificOutput"]["permissionDecisionReason"]
    assert not {c for c in reason if "가" <= c <= "힣"}, reason


# ---- A turn running with no consumer ----
# Even if the turn's consumer (the turn job, aipds/turn_job.py) is cancelled, the CLI turn
# keeps running and the reader keeps reading. In that state new turns are refused
# (has_live_turn), so a parked question must still be re-showable and that turn must be
# interruptible — otherwise the user is stuck.

@pytest.mark.asyncio
async def test_a_parked_question_is_not_treated_as_a_live_turn_by_run(tmp_path):
    """Why the guard is placed **after** the parked-question short-circuit.

    The reader of a parked turn is also alive (has_live_turn is true), so placing the guard
    first would turn the "answer first" notice and the re-display of the question form into a
    refusal.
    """
    d, _, _ = _driver(tmp_path, {"questions": True})
    [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    assert d.has_live_turn()

    events = [ev async for ev in d.run("또 보냄", {"session_id": "s-1"})]
    kinds = [e.kind for e in events]
    assert "error" not in kinds, events      # not a refusal;
    assert "questions" in kinds, events      # the question is shown again


@pytest.mark.asyncio
async def test_a_live_turn_with_no_consumer_can_still_be_interrupted(tmp_path):
    """**Preventing a dead end is the reason for this check.**

    Since `run()` started refusing an in-progress turn, a user who cannot interrupt that turn
    is stuck forever. But for a generating turn with no consumer, `_turn_active` is False and
    there is no parked question either, so interrupt misjudged it as "nothing to interrupt".
    """
    d, runner, cap = _runner(tmp_path, {"questions": True,
                                        "preface_texts": ["문장 1", "문장 2"]})
    agen = runner.send_message("hi").__aiter__()
    async for _ in agen:
        break
    await agen.aclose()
    await _reconnect_gap()
    assert d.has_live_turn()

    await runner.interrupt()
    # Observable evidence that the interrupt actually happened — with an early return this
    # marker is absent (the same assertion as test_interrupt_records_that_the_turn_was_stopped).
    from aipds.agent.claude_driver import INTERRUPTED_MARKER
    assert any(e.kind == "status" and e.text == INTERRUPTED_MARKER
               for e in d._queue), "소비자가 없으면 끊지 못했다"


# ---- There are no custom MCP tools (submit_document removed, 2026-08-21) ----


def test_discovery_advertises_no_custom_mcp_tools(tmp_path, monkeypatch):
    """`submit_document` was the only custom tool, and it is gone.

    The rationale for keeping it was "`version` and 'ready for review vs. intermediate save'
    are judgment, not parsing", but the actual instruction (discovery-config/CLAUDE.md) did not
    call for judgment — it said to call it every time a document is created or updated. With no
    judgment, the signal is 1:1 with "a document was written", and PostToolUse already sees
    that (agent/reconcile.document_events).

    **Why removing a single tool gets a test.** A tool has two costs: one inference round trip
    per call, and the tool description carried in the context on **every turn**. If it comes
    back, those costs return silently — that this tool remained even after `report_stage` and
    `handoff_prototype` moved to hooks is evidence of that silence.
    """
    options = _captured_options(tmp_path, monkeypatch,
                                {"session_id": "p1", "resume": False})
    assert not options.mcp_servers, (
        f"Discovery에 커스텀 MCP 서버가 남아 있다: {options.mcp_servers}")
    assert not [t for t in (options.allowed_tools or []) if "submit_document" in t], (
        f"allowed_tools에 submit_document가 남아 있다: {options.allowed_tools}")


@pytest.mark.asyncio
async def test_documents_already_on_disk_are_not_announced_as_new(tmp_path):
    """A resume turn must not announce every existing document as "updated".

    The `document` event is derived from a workspace scan (2026-08-21), so if a turn starts with
    an empty cursor, **every document already there looks like a new one.** The driver is what
    holds the cursor, so this comes on a new driver's first turn — the first attach after a
    backend restart is exactly that, and by then the tree is already populated. A project with
    ten documents announces ten times, and the update banner points not at the document just
    written but at whichever document is last in scan order.

    So at the start of the turn the cursor is **seeded** with the disk state: whatever exists
    at that point is "already seen", and only what changes during the turn is announced.
    """
    d, ws, _ = _driver(tmp_path, {"text": ["ok"]})
    for name in ("prfaq.md", "solution-analysis.md"):
        p = ws / "aiplc-docs" / "discovery" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {name}\n본문\n", encoding="utf-8")

    events = [ev async for ev in d.run("hi", {"session_id": "s-1"})]

    assert [e.payload for e in events if e.kind == "document"] == []


@pytest.mark.asyncio
async def test_a_document_written_during_the_turn_is_still_announced(tmp_path):
    """If seeding overreaches, nothing gets announced — the control case. A new project's first
    document is not on disk at seeding time, so it must be announced."""
    d, ws, _ = _driver(tmp_path, {"text": ["ok"]})

    async def write_during_turn():
        p = ws / "aiplc-docs" / "discovery" / "prfaq.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# PR/FAQ\n본문\n", encoding="utf-8")
        return await d._on_post_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": str(p)}}, "t1", None)

    events = []
    async for ev in d.run("hi", {"session_id": "s-1"}):
        if not events:
            await write_during_turn()
        events.append(ev)
    docs = [json.loads(e.payload) for e in events if e.kind == "document"]
    assert [x["path"] for x in docs] == ["aiplc-docs/discovery/prfaq.md"]
