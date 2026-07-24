import asyncio, json, pytest
from sdk_driver import SdkDriver
from tests.fake_sdk import FakeSdkClient, ResultMessage

ASK_INPUT = {"questions": [
    {"question": "Which DB?", "header": "DB",
     "options": [{"label": "Postgres", "description": "relational"},
                 {"label": "DynamoDB", "description": "NoSQL"}],
     "multiSelect": False},
]}

class QuestionScriptClient(FakeSdkClient):
    """Simulates the SDK: receive_response first triggers can_use_tool
    (captured from the driver), waits for its resolution, then finishes."""
    def __init__(self, driver_ref):
        super().__init__()
        self.driver_ref = driver_ref
        self.answer_result = None

    async def receive_response(self):
        result = await self.driver_ref()._on_can_use_tool(
            "AskUserQuestion", ASK_INPUT, None)
        self.answer_result = result
        yield ResultMessage()

@pytest.mark.asyncio
async def test_question_roundtrip(tmp_path):
    holder = {}
    client = QuestionScriptClient(lambda: holder["d"])
    d = SdkDriver(str(tmp_path), client_factory=lambda: client)
    holder["d"] = d

    async def consume():
        return [ev async for ev in d.run("build")]
    turn = asyncio.create_task(consume())

    # wait until the question event is queued and pending() reflects it
    for _ in range(100):
        await asyncio.sleep(0.01)
        if d._pending_payload is not None:
            break
    payload = json.loads(d._pending_payload)
    iid = payload["interrupt_id"]
    qf = payload["questions"]          # QuestionFile shape (frontend contract)
    assert qf["parse_ok"] is True
    q = qf["questions"][0]
    assert q["number"] == 1 and q["text"] == "Which DB?"
    assert [o["letter"] for o in q["options"]] == ["A", "B"]
    assert q["options"][0]["text"].startswith("Postgres")

    ok = await d.submit_answers(iid, {"1": "A"})   # letter, QuestionForm contract
    assert ok
    events = await turn
    kinds = [e.kind for e in events]
    assert "questions" in kinds and kinds[-1] == "done"
    ui = client.answer_result.updated_input
    assert ui["answers"] == {"Which DB?": "Postgres"}   # letter → SDK label
    assert ui["questions"] == ASK_INPUT["questions"]     # SDK originals passed through
    assert d._pending_payload is None

@pytest.mark.asyncio
async def test_answer_translation_variants(tmp_path):
    d = SdkDriver(str(tmp_path), client_factory=lambda: FakeSdkClient())
    opts = [{"label": "Postgres", "description": "r"},
            {"label": "DynamoDB", "description": "n"}]
    assert d._answer_to_sdk("A,B", opts) == "Postgres, DynamoDB"
    assert d._answer_to_sdk("A: use v16", opts) == "Postgres: use v16"
    assert d._answer_to_sdk("just use sqlite", opts) == "just use sqlite"

@pytest.mark.asyncio
async def test_answers_wrong_interrupt_id_rejected(tmp_path):
    d = SdkDriver(str(tmp_path), client_factory=lambda: FakeSdkClient())
    assert not await d.submit_answers("nope", {"1": "x"})

@pytest.mark.asyncio
async def test_interrupt_clears_pending_question(tmp_path):
    """interrupt() during a pending question must clear the pending state:
    a stale _pending_payload would make pending() report an unanswerable
    question after the turn was already aborted."""
    holder = {}
    client = QuestionScriptClient(lambda: holder["d"])
    d = SdkDriver(str(tmp_path), client_factory=lambda: client)
    holder["d"] = d

    async def consume():
        return [ev async for ev in d.run("build")]
    turn = asyncio.create_task(consume())

    for _ in range(100):
        await asyncio.sleep(0.01)
        if d._pending_payload is not None:
            break
    assert d._pending_payload is not None

    await d.interrupt()
    assert d._pending_payload is None
    assert await d.pending() is None
    assert not await d.submit_answers("whatever", {"1": "A"})

    turn.cancel()
    try:
        await turn
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_interrupt_during_question_yields_terminal_events(tmp_path):
    """A user-initiated interrupt while a question is pending must still end
    the stream with status:"interrupted" + done — NOT let CancelledError
    escape run() (the UI would show a dead connection for a deliberate
    stop). Final-review finding I3."""
    holder = {}
    client = QuestionScriptClient(lambda: holder["d"])
    d = SdkDriver(str(tmp_path), client_factory=lambda: client)
    holder["d"] = d

    events = []

    async def consume():
        async for ev in d.run("build"):
            events.append(ev)

    turn = asyncio.create_task(consume())
    for _ in range(100):
        await asyncio.sleep(0.01)
        if d._pending_payload is not None:
            break
    assert d._pending_payload is not None

    await d.interrupt()
    await turn  # must complete cleanly — no CancelledError escapes

    kinds = [e.kind for e in events]
    assert kinds[-1] == "done"
    assert ("status", "interrupted") in [(e.kind, e.text) for e in events]
    assert d._turn_active is False


@pytest.mark.asyncio
async def test_external_cancel_still_propagates(tmp_path):
    """A genuine consumer-side cancellation (not our interrupt) must
    propagate as CancelledError — only interrupt-triggered cancellation is
    converted to terminal events."""
    holder = {}
    client = QuestionScriptClient(lambda: holder["d"])
    d = SdkDriver(str(tmp_path), client_factory=lambda: client)
    holder["d"] = d

    async def consume():
        return [ev async for ev in d.run("build")]

    turn = asyncio.create_task(consume())
    for _ in range(100):
        await asyncio.sleep(0.01)
        if d._pending_payload is not None:
            break
    turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn
    assert d._turn_active is False


@pytest.mark.asyncio
async def test_abandoned_generator_cancels_pending_receive(tmp_path):
    """SSE client disconnect (generator aclose) must cancel the in-flight
    __anext__ future — otherwise asyncio reports a destroyed pending task.
    Final-review finding I2."""
    from tests.fake_sdk import AssistantMessage, TextBlock

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
    d = SdkDriver(str(tmp_path), client_factory=lambda: client)
    events = []

    async def consume():
        async for ev in d.run("go"):
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
    assert d._turn_active is False
