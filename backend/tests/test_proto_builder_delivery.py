# backend/tests/test_proto_builder_delivery.py — the message-loss defect in
# PrototypeBuilder.run()'s queue relay, and the ownership rule that fixes it.
#
# The defect: run() batch-popped the whole event queue into a local list
# (drain_queue()) and yielded the items one at a time. A consumer abandoning the
# generator partway through that sequence -- SSE client disconnect, proxy
# timeout, user navigating away, all routine for
# routes/prototypes.py's EventSourceResponse -- destroyed the popped-but-not-yet
# -yielded remainder with the generator frame. Not in the queue for the next
# turn, not anywhere else.
#
# The rule that replaces it (carried from claude_driver._relay_queue, where it
# survived five review rounds):
#
#     An event lives in exactly one place that outlives the generator, and it
#     leaves that place only after the consumer has actually received it --
#     queue[0] -> yield -> pop(0). Reaching the line after the yield IS the
#     proof of delivery.
#
# METHOD NOTE, learned expensively on the sibling file: three times over there a
# test looked green while silently not exercising its window. So every test here
# asserts on WHAT THE CONSUMER RECEIVED (across the abandoned turn AND the next
# one), not on the builder's end state -- a `_queue` assertion alone goes vacuous
# the moment the relay stops popping at all. Each also pins that the abandonment
# actually happened, so the premise can't quietly evaporate. Every one of them
# was verified to FAIL against the reverted implementation.
from __future__ import annotations

import asyncio

import pytest

from pathfinder.models import AgentEvent
from pathfinder.proto.builder import PrototypeBuilder
from fakes.fake_sdk import (AssistantMessage, FakeSdkClient, ResultMessage,
                            TextBlock)

ASK_INPUT = {"questions": [
    {"question": "Which DB?", "header": "DB",
     "options": [{"label": "Postgres", "description": "relational"}],
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


class PerTurnClient(FakeSdkClient):
    """One message stream per query(), like the real SDK.

    FakeSdkClient replays its whole `script` on EVERY receive_response(), which
    makes a lost message look delivered on the next turn. That artifact hid the
    message-side half of this defect, so scripts here are per-turn.
    """

    def __init__(self, turns: list[list]):
        super().__init__()
        self._turns = turns
        self.turn_count = 0

    async def receive_response(self):
        self.turn_count += 1
        for msg in (self._turns[self.turn_count - 1]
                    if self.turn_count <= len(self._turns) else []):
            yield msg


async def _queue_writes(builder, *names) -> None:
    """Queue file_changed events the way production does -- through the real
    PostToolUse hook, not by poking `_queue`. One MultiEdit, or a burst of
    Write calls, lands exactly this shape."""
    for i, name in enumerate(names):
        await builder._on_post_tool_use(
            {"tool_name": "Write",
             "tool_input": {"file_path": f"{builder._workspace}/prototype/{name}"}},
            f"toolu_{i}", None)


# ---- the defect itself: call site 1, the normal turn ----

async def test_queued_events_not_yet_yielded_survive_abandonment(tmp_path):
    """THE reproduction, at call site 1 (builder.py's per-poll relay).

    Three file_changed events in one burst, consumer takes the first and
    disconnects. Measured against the batch-pop version: `_queue == []` and
    p2/p3 reached no consumer on this turn or any later one -- the prototype
    tab's artifact list silently disagreed with what the agent wrote
    (usePrototypeStream.ts:68-70 builds `changedPaths` from exactly these).
    """
    client = PerTurnClient([[ResultMessage()], [ResultMessage()]])
    b = _builder(tmp_path, client)
    await _queue_writes(b, "p1.js", "p2.js", "p3.js")

    # Turn 1: the consumer takes ONE event, then the SSE client goes away.
    turn1 = []
    agen = b.run("go").__aiter__()
    turn1.append(await agen.__anext__())
    await agen.aclose()

    # The premise: abandonment really happened mid-sequence. Without this the
    # assertions below could pass on a relay that simply ran to completion.
    assert [(e.kind, e.path) for e in turn1] == [
        ("file_changed", "prototype/p1.js")], turn1

    # Turn 2: a fresh consumer (the user reconnects / sends again).
    turn2 = [ev async for ev in b.run("again")]

    delivered = [e.path for e in turn1 + turn2 if e.kind == "file_changed"]
    for name in ("prototype/p2.js", "prototype/p3.js"):
        assert name in delivered, delivered
    assert turn2[-1].kind == "done"


async def test_an_abandoned_relay_leaves_the_remainder_owned(tmp_path):
    """The mechanism behind the test above, pinned separately: the remainder is
    still OWNED, not merely re-derivable. Kept as its own test because the
    delivery assertion alone would also pass if some later turn happened to
    re-emit the same paths for an unrelated reason."""
    client = PerTurnClient([[ResultMessage()]])
    b = _builder(tmp_path, client)
    await _queue_writes(b, "p1.js", "p2.js", "p3.js")

    agen = b.run("go").__aiter__()
    first = await agen.__anext__()
    await agen.aclose()

    assert (first.kind, first.path) == ("file_changed", "prototype/p1.js")
    # Head included: the item being delivered when the consumer vanished is not
    # popped either (at-least-once -- see the duplicate-safety note below).
    assert [e.path for e in b._queue] == [
        "prototype/p1.js", "prototype/p2.js", "prototype/p3.js"]


# ---- call site 4: the terminal harvest ----

async def test_queued_events_reach_the_consumer_before_done(tmp_path):
    """sse.ts:29 / prototypes.ts:164 close the EventSource on `done`, so
    anything the backend writes after it never reaches onEvent.

    The old shape yielded `done` the moment _translate produced it and drained
    the queue AFTERWARDS (old call site 4, post-`finally`) -- so a tool event
    queued during the turn went out behind the terminal event and was dropped
    client-side. This is the ordering half of the same defect.
    """
    client = PerTurnClient([[
        AssistantMessage(content=[TextBlock(text="writing")]),
        ResultMessage(),
    ]])
    b = _builder(tmp_path, client)
    await _queue_writes(b, "p1.js")

    kinds = [e.kind for e in [ev async for ev in b.run("go")]]

    assert "file_changed" in kinds, kinds
    assert kinds.index("file_changed") < kinds.index("done"), kinds
    assert kinds[-1] == "done", kinds
    assert kinds.count("done") == 1, kinds


async def test_an_event_queued_while_the_consumer_is_reading_still_precedes_done(
        tmp_path):
    """An event queued mid-stream, from inside the consumer's own loop.

    SCOPE, stated honestly: this pins call site 1's relay plus the held
    terminal, NOT the defensive drain at call site 4. Verified by mutation --
    moving that drain to AFTER the terminal event leaves this test (and all 14)
    green, because site 1 drains the queue to exhaustion at the top of every
    pass and nothing can append between its last drain and the loop's `break`.
    Instrumented across the whole suite: site 4 is reached 13 times with an
    empty queue every time. See the comment there; the claim this test does NOT
    make is deliberate.
    """
    client = PerTurnClient([[
        AssistantMessage(content=[TextBlock(text="writing")]),
        ResultMessage(),
    ]])
    b = _builder(tmp_path, client)
    await _queue_writes(b, "p1.js")

    kinds = []
    async for ev in b.run("go"):
        kinds.append(ev.kind)
        if ev.kind == "file_changed" and len(kinds) < 5:
            # The builder is suspended in the harvest's yield right now.
            await _queue_writes(b, f"late{len(kinds)}.js")

    assert kinds.count("file_changed") >= 2, kinds
    assert kinds[-1] == "done", kinds
    assert kinds.count("done") == 1, kinds
    # Nothing stranded: every queued event was relayed before the terminal.
    assert b._queue == []


# ---- call site 3: the error path ----

class QueryFails(FakeSdkClient):
    """Dies in `query()` -- BEFORE the poll loop is entered.

    This is what makes site 3 the ONLY exit for events already owned by the
    queue, and it is why these two tests use it instead of a receive_response()
    that raises. Verified by mutation: with a receive_response failure, site 1's
    relay has already run and drained the queue, so site 3 is reached EMPTY and
    reverting it changes nothing observable -- the first draft of both tests
    passed against a batch-popped site 3.

    How it is reached in production: a previous turn was abandoned mid-relay,
    leaving items owned (which this fix is what makes possible), and the next
    turn then dies in query() -- a dead subprocess, a transport error.
    """

    async def query(self, text):
        raise RuntimeError("AWS_SECRET=xyz transport died")


async def test_the_error_path_relays_the_queue_before_the_error(tmp_path):
    """sse.ts:29 / prototypes.ts:164 close the stream on `error` as well as on
    `done`, so the queue has to go out FIRST here too -- work the agent really
    did, reported after the frame that closes the client, is work the prototype
    tab never shows."""
    b = _builder(tmp_path, QueryFails())
    await _queue_writes(b, "p1.js", "p2.js", "p3.js")

    got = [(e.kind, e.path) for e in [ev async for ev in b.run("go")]]

    assert got == [("file_changed", "prototype/p1.js"),
                   ("file_changed", "prototype/p2.js"),
                   ("file_changed", "prototype/p3.js"),
                   ("error", None)], got
    # Sanitized: the raw exception text never reaches the user.
    assert "xyz" not in str(got)


async def test_the_error_path_does_not_strand_the_queue_on_abandonment(tmp_path):
    """Same path, consumer leaving mid-relay. Asserted separately from the
    ordering test above because that one's consumer never abandons, so a batch
    pop survives it untouched."""
    b = _builder(tmp_path, QueryFails())
    await _queue_writes(b, "p1.js", "p2.js", "p3.js")

    seen = []
    agen = b.run("go").__aiter__()
    async for ev in agen:
        seen.append((ev.kind, ev.path))
        break                      # abandoned inside the error path's relay
    await agen.aclose()

    # (1) the relay really ran and the consumer really left mid-sequence
    assert seen == [("file_changed", "prototype/p1.js")], seen
    # (2) not a batch pop -- the remainder is still owned
    assert [e.path for e in b._queue] == [
        "prototype/p1.js", "prototype/p2.js", "prototype/p3.js"]
    # (3) the terminal `error` never got ahead of the queue
    assert "error" not in [k for k, _ in seen], seen


# ---- call site 2: the interrupt path ----

class QuestionThenHang(FakeSdkClient):
    """Raises an AskUserQuestion through the real can_use_tool callback, then
    stays silent -- the actual shape while a question is parked (the CLI is
    blocked on the permission response and receive_response yields nothing)."""

    def __init__(self, builder_ref):
        super().__init__()
        self.builder_ref = builder_ref

    async def receive_response(self):
        await self.builder_ref()._on_can_use_tool("AskUserQuestion", ASK_INPUT, None)
        yield ResultMessage()


async def _await_pending(builder) -> None:
    for _ in range(200):
        await asyncio.sleep(0.01)
        if builder._pending_payload is not None:
            return
    raise AssertionError("question never became pending")


class InterruptDuringQuery(FakeSdkClient):
    """The stop lands in `query()`, before the poll loop is ever entered.

    Site 2's counterpart to `QueryFails`, and needed for the same measured
    reason: when the interrupt arrives the ordinary way (mid-turn, question
    parked), site 1's relay has already drained the queue, so site 2 is reached
    EMPTY -- instrumented across the full suite, all 3 hits had `q=[]`, and
    reverting site 2 to a batch pop changed nothing observable. Failing here
    instead is what leaves items owned with site 2 as their only exit.

    Sets `_interrupted` exactly as `interrupt()` does, then cancels -- that flag
    is what tells `run()` this is OUR stop rather than the consumer's.
    """

    def __init__(self, builder_ref):
        super().__init__()
        self.builder_ref = builder_ref

    async def query(self, text):
        self.builder_ref()._interrupted = True
        raise asyncio.CancelledError()


async def test_the_interrupt_path_relays_owned_events_before_its_terminals(
        tmp_path):
    """Site 2 with a queue that only site 2 can drain (see InterruptDuringQuery).

    Reached in production when a previous turn was abandoned mid-relay -- which
    is exactly what the ownership fix now allows -- and the user stops the next
    one before its first message arrives.
    """
    holder = {}
    b = _builder(tmp_path, InterruptDuringQuery(lambda: holder["b"]))
    holder["b"] = b
    await _queue_writes(b, "p1.js", "p2.js", "p3.js")

    got = [(e.kind, e.path or e.text) for e in [ev async for ev in b.run("go")]]

    assert got == [("file_changed", "prototype/p1.js"),
                   ("file_changed", "prototype/p2.js"),
                   ("file_changed", "prototype/p3.js"),
                   ("status", "interrupted"),
                   ("done", None)], got


async def test_the_interrupt_path_does_not_strand_owned_events_on_abandonment(
        tmp_path):
    """Same path, consumer leaving mid-relay -- the batch-pop window itself."""
    holder = {}
    b = _builder(tmp_path, InterruptDuringQuery(lambda: holder["b"]))
    holder["b"] = b
    await _queue_writes(b, "p1.js", "p2.js", "p3.js")

    seen = []
    agen = b.run("go").__aiter__()
    async for ev in agen:
        seen.append((ev.kind, ev.path))
        break                     # abandoned inside the interrupt path's relay
    await agen.aclose()

    assert seen == [("file_changed", "prototype/p1.js")], seen
    assert [e.path for e in b._queue] == [
        "prototype/p1.js", "prototype/p2.js", "prototype/p3.js"]
    assert "done" not in [k for k, _ in seen], seen


async def test_an_interrupt_mid_turn_relays_the_queue_before_its_terminal_events(
        tmp_path):
    """The ordinary interrupt shape (question parked, user hits stop) end-to-end.

    Does NOT pin site 2 -- site 1 drains first here, as measured -- but it does
    pin the user-visible contract this path exists for: the writes are reported,
    the stream ends with status:interrupted then done, and no CancelledError
    escapes to leave the UI on a dead connection.
    """
    holder = {}
    b = _builder(tmp_path, QuestionThenHang(lambda: holder["b"]))
    holder["b"] = b

    events = []

    async def consume():
        async for ev in b.run("go"):
            events.append(ev)

    turn = asyncio.create_task(consume())
    await _await_pending(b)
    await _queue_writes(b, "p1.js", "p2.js", "p3.js")
    await b.interrupt()
    await turn                       # must end cleanly, not raise

    kinds = [e.kind for e in events]
    paths = [e.path for e in events if e.kind == "file_changed"]
    assert paths == ["prototype/p1.js", "prototype/p2.js", "prototype/p3.js"], kinds
    # Both terminal markers come after every queued event.
    assert kinds[-2:] == ["status", "done"], kinds
    assert max(i for i, k in enumerate(kinds) if k == "file_changed") \
        < kinds.index("done"), kinds
    assert kinds.count("done") == 1, kinds


class InterruptReturnsResultMessage(FakeSdkClient):
    """The REAL SDK's stop shape: `client.interrupt()` makes the CLI halt the
    turn and emit a ResultMessage, so run()'s loop exits NORMALLY with
    `_interrupted` set -- it never goes through CancelledError.

    Worth its own double because the fakes elsewhere in the suite only ever
    reach the interrupt handling via cancellation, which left the normal-exit
    branch unexercised: instrumented across the whole suite, the site-4
    `interrupted` marker was reached zero times, and deleting it broke nothing.
    The original code emitted that marker (`if ev.kind == "done" and
    self._interrupted`) so this is preserved behavior, not new behavior.
    """

    def __init__(self):
        super().__init__()
        self.stop = asyncio.Event()

    async def interrupt(self):
        self.interrupt_calls += 1
        self.stop.set()

    async def receive_response(self):
        yield AssistantMessage(content=[TextBlock(text="working")])
        await self.stop.wait()
        yield ResultMessage()


async def test_a_stop_that_ends_the_turn_normally_still_marks_it_interrupted(
        tmp_path):
    """status:"interrupted" must still precede `done` when the stop arrives as a
    ResultMessage rather than a cancellation -- otherwise a user-initiated stop
    is indistinguishable from a completed build in the transcript."""
    client = InterruptReturnsResultMessage()
    b = _builder(tmp_path, client)

    events = []

    async def consume():
        async for ev in b.run("go"):
            events.append(ev)

    turn = asyncio.create_task(consume())
    for _ in range(200):                 # let the first message land
        await asyncio.sleep(0.01)
        if events:
            break
    await _queue_writes(b, "a.js")
    await b.interrupt()
    await turn

    got = [(e.kind, e.path or e.text) for e in events]
    assert got == [("message", "working"),
                   ("file_changed", "prototype/a.js"),
                   ("status", "interrupted"),
                   ("done", None)], got


# ---- exactly one terminal event, on every path ----

async def test_a_stream_ending_without_a_result_message_still_terminates(tmp_path):
    """Zero terminal events hangs the SSE client forever: sse.ts only closes on
    done/error/transport failure, so the spinner never stops and
    usePrototypeStream's `streaming` never clears -- which also blocks the next
    send (its `stopRef.current` guard).

    The message stream ending with no ResultMessage is reachable: the claude
    subprocess exiting cleanly mid-turn closes the iterator without one.
    Measured on the original code: `events == []`, zero terminal events.
    """
    b = _builder(tmp_path, PerTurnClient([[
        AssistantMessage(content=[TextBlock(text="partial")]),
    ]]))

    events = [ev async for ev in b.run("go")]

    kinds = [e.kind for e in events]
    assert [k for k in kinds if k in ("done", "error")] == ["done"], kinds
    assert kinds[-1] == "done", kinds


@pytest.mark.parametrize("path", ["normal", "no_result", "error", "busy"])
async def test_every_path_yields_exactly_one_terminal_event(tmp_path, path):
    """The enumeration, as a test rather than only as prose: whatever run()
    does, a consumer that reads to exhaustion sees exactly one done/error and
    it is last. Zero hangs the client; two make the frontend finish a turn it
    is still receiving."""
    class Boom(FakeSdkClient):
        async def receive_response(self):
            raise RuntimeError("died")
            yield  # pragma: no cover

    if path == "normal":
        b = _builder(tmp_path, PerTurnClient([[
            AssistantMessage(content=[TextBlock(text="hi")]), ResultMessage()]]))
    elif path == "no_result":
        b = _builder(tmp_path, PerTurnClient([[
            AssistantMessage(content=[TextBlock(text="hi")])]]))
    elif path == "error":
        b = _builder(tmp_path, Boom())
    else:
        b = _builder(tmp_path, PerTurnClient([[ResultMessage()]]))
        b._turn_active = True

    kinds = [e.kind for e in [ev async for ev in b.run("go")]]

    terminals = [k for k in kinds if k in ("done", "error")]
    assert len(terminals) == 1, (path, kinds)
    assert kinds[-1] == terminals[0], (path, kinds)


# ---- the counterweight: a dead question card must not be relayed ----

async def test_interrupt_drops_a_question_card_left_owned_by_the_queue(tmp_path):
    """The one place delivery-then-pop needs a counterweight.

    An abandoned turn correctly leaves its `questions` event owned, so the next
    turn relays it -- that is the fix. But once interrupt() has cancelled the
    future behind it, no submit_answers can ever resolve that question: relaying
    it hands the user a live-looking card whose answer gets a 409 from
    routes/prototypes.py. Dropped where the answerer dies, never merely because
    a consumer walked away. Same two lines as claude_driver.py:1242.
    """
    holder = {}
    b = _builder(tmp_path, QuestionThenHang(lambda: holder["b"]))
    holder["b"] = b

    # The generator is held SUSPENDED at the yield that delivered `questions`,
    # which is the live shape: the user hits stop while the card is on screen,
    # and interrupt() arrives on a separate request with the turn still open.
    # (Letting a consume() coroutine return instead would finalize the generator
    # first, and interrupt() would no-op on its `_turn_active` guard -- which is
    # how the first draft of this test passed vacuously.)
    agen = b.run("go").__aiter__()
    delivered = await agen.__anext__()
    assert delivered.kind == "questions"                  # premise
    assert b._turn_active is True                         # premise: turn is live
    assert any(e.kind == "questions" for e in b._queue)   # premise: still owned

    await b.interrupt()

    assert not any(e.kind == "questions" for e in b._queue)
    assert await b.pending() is None
    await agen.aclose()


async def test_disconnect_drops_a_question_card_and_a_later_turn_does_not_show_it(
        tmp_path):
    """The disconnect twin of the test above, asserted through what the NEXT
    turn's consumer receives -- the idle timer and close() both land here, and
    the question's future dies with the subprocess."""
    holder = {}
    client = QuestionThenHang(lambda: holder["b"])
    b = _builder(tmp_path, client)
    holder["b"] = b

    turn1 = []
    agen = b.run("go").__aiter__()
    async for ev in agen:
        turn1.append(ev.kind)
        if ev.kind == "questions":
            break
    await agen.aclose()
    assert "questions" in turn1                            # premise
    assert any(e.kind == "questions" for e in b._queue)    # premise: owned

    await b.disconnect()
    assert await b.pending() is None

    # A brand-new turn (new subprocess) must not surface the dead card.
    b._factory = lambda: PerTurnClient([[
        AssistantMessage(content=[TextBlock(text="fresh")]), ResultMessage()]])
    kinds = [e.kind for e in [ev async for ev in b.run("again")]]
    assert "questions" not in kinds, kinds
    assert kinds == ["message", "done"], kinds
