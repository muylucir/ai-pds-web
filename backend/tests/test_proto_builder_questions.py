# backend/tests/test_proto_builder_questions.py — ported from
# harness/tests/test_sdk_driver_questions.py. The driver logic is unchanged;
# only its home and constructor moved.
from __future__ import annotations

import asyncio
import json

import pytest

from pathfinder.proto.builder import PrototypeBuilder
from fakes.fake_sdk import FakeSdkClient, ResultMessage

ASK_INPUT = {"questions": [
    {"question": "Which DB?", "header": "DB",
     "options": [{"label": "Postgres", "description": "relational"},
                 {"label": "DynamoDB", "description": "NoSQL"}],
     "multiSelect": False},
]}


def _builder(tmp_path, client, **kw):
    return PrototypeBuilder(
        workspace=str(tmp_path),
        config_dir=str(tmp_path / "config"),
        session_id="11111111-2222-3333-4444-555555555555",
        resume=False,
        client_factory=lambda: client,
        **kw,
    )


class QuestionScriptClient(FakeSdkClient):
    """Simulates the SDK: receive_response first triggers can_use_tool
    (captured from the builder), waits for its resolution, then finishes."""
    def __init__(self, builder_ref):
        super().__init__()
        self.builder_ref = builder_ref
        self.answer_result = None

    async def receive_response(self):
        result = await self.builder_ref()._on_can_use_tool(
            "AskUserQuestion", ASK_INPUT, None)
        self.answer_result = result
        yield ResultMessage()


async def test_question_roundtrip(tmp_path):
    holder = {}
    client = QuestionScriptClient(lambda: holder["b"])
    b = _builder(tmp_path, client)
    holder["b"] = b

    async def consume():
        return [ev async for ev in b.run("build")]
    turn = asyncio.create_task(consume())

    # wait until the question event is queued and pending() reflects it
    for _ in range(100):
        await asyncio.sleep(0.01)
        if b._pending_payload is not None:
            break
    payload = json.loads(b._pending_payload)
    iid = payload["interrupt_id"]
    qf = payload["questions"]          # QuestionFile shape (frontend contract)
    assert qf["parse_ok"] is True
    q = qf["questions"][0]
    assert q["number"] == 1 and q["text"] == "Which DB?"
    assert [o["letter"] for o in q["options"]] == ["A", "B"]
    assert q["options"][0]["text"].startswith("Postgres")

    ok = await b.submit_answers(iid, {"1": "A"})   # letter, QuestionForm contract
    assert ok
    events = await turn
    kinds = [e.kind for e in events]
    assert "questions" in kinds and kinds[-1] == "done"
    ui = client.answer_result.updated_input
    assert ui["answers"] == {"Which DB?": "Postgres"}   # letter → SDK label
    assert ui["questions"] == ASK_INPUT["questions"]     # SDK originals passed through
    assert b._pending_payload is None


async def test_zero_option_question_is_denied_not_raised(tmp_path):
    """리뷰 finding 2: question_file_from_sdk는 옵션 없는 질문에 ValueError를
    던진다(정규화 계약). 옛 _to_question_file은 절대 그러지 않았으므로
    _on_can_use_tool에는 그 예외를 감쌀 코드가 없었다 -- bypassPermissions
    아래에서 무인으로 도는 프로토타입 빌드가 처리 경로 없이 죽는 걸 막는다.
    ask_questions(tools.py)가 ValueError를 잡아 모델이 읽을 문자열을 돌려주는
    것과 대응되도록, can_use_tool 콜백은 PermissionResultDeny로 거부하고
    모델이 재시도할 수 있는 메시지를 message에 담는다."""
    from claude_agent_sdk.types import PermissionResultDeny

    b = _builder(tmp_path, FakeSdkClient())
    bad_input = {"questions": [{"question": "q", "options": []}]}
    result = await b._on_can_use_tool("AskUserQuestion", bad_input, None)
    assert isinstance(result, PermissionResultDeny)
    assert result.message
    assert b._pending_payload is None


async def test_answer_translation_variants(tmp_path):
    b = _builder(tmp_path, FakeSdkClient())
    opts = [{"label": "Postgres", "description": "r"},
            {"label": "DynamoDB", "description": "n"}]
    assert b._answer_to_sdk("A,B", opts) == "Postgres, DynamoDB"
    assert b._answer_to_sdk("A: use v16", opts) == "Postgres: use v16"
    assert b._answer_to_sdk("just use sqlite", opts) == "just use sqlite"


async def test_answers_wrong_interrupt_id_rejected(tmp_path):
    b = _builder(tmp_path, FakeSdkClient())
    assert not await b.submit_answers("nope", {"1": "x"})


async def test_interrupt_clears_pending_question(tmp_path):
    """interrupt() during a pending question must clear the pending state:
    a stale _pending_payload would make pending() report an unanswerable
    question after the turn was already aborted."""
    holder = {}
    client = QuestionScriptClient(lambda: holder["b"])
    b = _builder(tmp_path, client)
    holder["b"] = b

    async def consume():
        return [ev async for ev in b.run("build")]
    turn = asyncio.create_task(consume())

    for _ in range(100):
        await asyncio.sleep(0.01)
        if b._pending_payload is not None:
            break
    assert b._pending_payload is not None

    await b.interrupt()
    assert b._pending_payload is None
    assert await b.pending() is None
    assert not await b.submit_answers("whatever", {"1": "A"})

    turn.cancel()
    try:
        await turn
    except (asyncio.CancelledError, Exception):
        pass


async def test_interrupt_during_question_yields_terminal_events(tmp_path):
    """A user-initiated interrupt while a question is pending must still end
    the stream with status:"interrupted" + done — NOT let CancelledError
    escape run() (the UI would show a dead connection for a deliberate
    stop). Final-review finding I3."""
    holder = {}
    client = QuestionScriptClient(lambda: holder["b"])
    b = _builder(tmp_path, client)
    holder["b"] = b

    events = []

    async def consume():
        async for ev in b.run("build"):
            events.append(ev)

    turn = asyncio.create_task(consume())
    for _ in range(100):
        await asyncio.sleep(0.01)
        if b._pending_payload is not None:
            break
    assert b._pending_payload is not None

    await b.interrupt()
    await turn  # must complete cleanly — no CancelledError escapes

    kinds = [e.kind for e in events]
    assert kinds[-1] == "done"
    assert ("status", "interrupted") in [(e.kind, e.text) for e in events]
    assert b._turn_active is False


async def test_external_cancel_still_propagates(tmp_path):
    """A genuine consumer-side cancellation (not our interrupt) must
    propagate as CancelledError — only interrupt-triggered cancellation is
    converted to terminal events."""
    holder = {}
    client = QuestionScriptClient(lambda: holder["b"])
    b = _builder(tmp_path, client)
    holder["b"] = b

    async def consume():
        return [ev async for ev in b.run("build")]

    turn = asyncio.create_task(consume())
    for _ in range(100):
        await asyncio.sleep(0.01)
        if b._pending_payload is not None:
            break
    turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn
    assert b._turn_active is False


async def test_abandoned_generator_cancels_pending_receive(tmp_path):
    """SSE client disconnect (generator aclose) must cancel the in-flight
    __anext__ future — otherwise asyncio reports a destroyed pending task.
    Final-review finding I2."""
    from fakes.fake_sdk import AssistantMessage, TextBlock

    class SlowClient(FakeSdkClient):
        def __init__(self):
            super().__init__()
            self.inflight = None

        async def receive_response(self):
            yield AssistantMessage(content=[TextBlock(text="one")])
            fut = asyncio.get_running_loop().create_future()
            self.inflight = fut
            await fut  # hangs until cancelled
            yield ResultMessage()

    client = SlowClient()
    b = _builder(tmp_path, client)
    events = []

    async def consume():
        async for ev in b.run("go"):
            events.append(ev)

    turn = asyncio.create_task(consume())
    # Wait until run() is blocked in its poll loop with a pending __anext__
    # that has entered receive_response and is hanging on the future.
    for _ in range(200):
        await asyncio.sleep(0.01)
        if client.inflight is not None:
            break
    assert client.inflight is not None
    assert events and events[0].kind == "message"

    # Abandon the consumer (SSE client disconnect) — external cancellation.
    turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn
    await asyncio.sleep(0.1)
    # The hanging receive_response future must have been cancelled through
    # the teardown chain (finally -> next_msg.cancel() -> agen unwinds) --
    # a leaked pending task logs "Task was destroyed but it is pending!".
    assert client.inflight.cancelled()
    assert b._turn_active is False
