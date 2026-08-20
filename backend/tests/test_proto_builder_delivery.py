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

from aipds.models import AgentEvent
from aipds.proto.builder import PrototypeBuilder
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


# ---- review round 2: the head-mutating producer, and the answered card ----

async def test_a_head_mutation_during_a_suspended_relay_does_not_eat_the_next_event(
        tmp_path):
    """The `queue[0] is ev` guard in `_relay_queue` is LOAD-BEARING.

    An earlier docstring claimed it was merely defensive because producers "only
    ever append". This diff itself introduced a head-MUTATING producer:
    `_drop_dead_question_cards()` rewrites the list in place, and `interrupt()`
    calls it from a separate `POST /interrupt` request while the turn's generator
    is parked mid-delivery.

    The window, reproduced here exactly: the card is delivered and the relay
    suspends with it still at `queue[0]`; the agent's last `Write` queues
    `realwork.js` BEHIND it; the user hits stop; the drop removes the head and
    shifts `realwork.js` into slot 0. The guard sees `queue[0] is not ev`, pops
    nothing, and `realwork.js` is delivered. With an unconditional `pop(0)` it is
    destroyed outright -- and the rest of the suite stays green, which is exactly
    why this test exists.
    """
    holder = {}
    b = _builder(tmp_path, QuestionThenHang(lambda: holder["b"]))
    holder["b"] = b

    received = []
    agen = b.run("go").__aiter__()
    while True:                        # pull until the card is delivered
        ev = await agen.__anext__()
        received.append(ev)
        if ev.kind == "questions":
            break
    # Premise: the relay is suspended with the card still owned at the head.
    assert [e.kind for e in b._queue] == ["questions"]

    # Real work lands behind the card while we are parked here.
    await _queue_writes(b, "realwork.js")
    assert [e.kind for e in b._queue] == ["questions", "file_changed"]

    # The stop arrives on its own request and MUTATES THE HEAD.
    await b.interrupt()
    assert [e.path for e in b._queue] == ["prototype/realwork.js"], \
        "premise: the drop must have shifted realwork.js into slot 0"

    async for ev in agen:
        received.append(ev)

    # The assertion that matters: what the consumer actually received.
    paths = [e.path for e in received if e.kind == "file_changed"]
    assert "prototype/realwork.js" in paths, [
        (e.kind, e.path or e.text) for e in received]
    assert [e.kind for e in received][-1] == "done"


class DetachedQuestionClient(FakeSdkClient):
    """Faithful to the SDK on the one point this test turns on: `can_use_tool`
    runs on a DETACHED task (claude_agent_sdk/_internal/query.py:231
    `spawn_detached`), NOT on the generator relaying the turn.

    That is what makes the stale-card window real: the consumer can abandon
    `run()` while the pending future stays alive, so a later `submit_answers`
    still succeeds and the turn genuinely continues.
    """

    def __init__(self, builder_ref):
        super().__init__()
        self.builder_ref = builder_ref
        self.turns = 0

    async def receive_response(self):
        self.turns += 1
        if self.turns == 1:
            b = self.builder_ref()
            asyncio.ensure_future(
                b._on_can_use_tool("AskUserQuestion", ASK_INPUT, None))
            for _ in range(200):        # let the callback queue its card
                await asyncio.sleep(0.01)
                if b._pending_payload is not None:
                    break
            await asyncio.sleep(3600)   # CLI blocked on the permission response
        else:
            yield AssistantMessage(content=[TextBlock(text="turn2 reply")])
            yield ResultMessage()
        return
        yield  # pragma: no cover


async def test_a_successfully_answered_question_card_is_not_re_shown(tmp_path):
    """The third drop point (claude_driver.py:1181-1183 has it; this file did not).

    A regression this ownership change introduced: on `main` the batch pop had
    already destroyed the event, so the next turn was clean. Now the card is
    correctly left owned when the stream dies mid-delivery -- but answering it
    successfully has to retire it, or the next turn re-shows a question the user
    already answered, and answering that returns False -> 409
    (routes/prototypes.py:212) -> "답변을 제출하지 못했습니다".
    """
    import json

    holder = {}
    b = _builder(tmp_path, DetachedQuestionClient(lambda: holder["b"]))
    holder["b"] = b

    # Turn 1: the card is delivered, then the SSE stream dies AT that yield.
    agen = b.run("go").__aiter__()
    turn1 = []
    while True:
        ev = await agen.__anext__()
        turn1.append(ev)
        if ev.kind == "questions":
            break
    await agen.aclose()
    assert [e.kind for e in turn1] == ["questions"]         # premise
    assert any(e.kind == "questions" for e in b._queue)     # premise: owned

    # The future is still live (detached task), so the answer really lands.
    iid = json.loads(b._pending_payload)["interrupt_id"]
    assert await b.submit_answers(iid, {"1": "A"}) is True

    # Turn 2 must not re-show it.
    turn2 = [ev async for ev in b.run("again")]
    kinds = [e.kind for e in turn2]
    assert "questions" not in kinds, kinds
    assert kinds[-1] == "done", kinds


async def test_answering_one_round_does_not_retire_a_different_rounds_card(
        tmp_path):
    """The drop is keyed by interrupt id, not by kind.

    `_drop_dead_question_cards` is unconditional because its callers kill the
    whole turn; this one must not be.

    THE `other` CARD IS MARKED DELIVERED, and that is the whole point of the
    setup. An earlier version left it unmarked, which made this test vacuous:
    round 3's `_was_delivered(ev)` predicate saved it on its own, so mutating the
    keying condition away left the suite green (measured: 656 passed). Keying is
    load-bearing in exactly ONE shape -- two DELIVERED question cards owned at
    once -- so that is the shape this test has to build.

    On what the keying buys, stated as measured rather than as hoped: the
    surviving card is NOT answerable, because `_on_can_use_tool` overwrites the
    single `_pending_iid` slot and a later round makes an earlier one return False
    from `submit_answers`. The keying is defensiveness -- keeping another round's
    event is a strictly narrower blast radius than dropping it, and this method
    should not silently depend on `_pending_iid` being single-slot. See
    `_drop_answered_question_card`'s docstring.
    """
    import json

    from aipds.proto.builder import _mark_delivered, _was_delivered

    holder = {}
    b = _builder(tmp_path, DetachedQuestionClient(lambda: holder["b"]))
    holder["b"] = b

    agen = b.run("go").__aiter__()
    while True:
        ev = await agen.__anext__()
        if ev.kind == "questions":
            break
    await agen.aclose()

    # A second, unrelated round's card also sitting owned in the queue -- and
    # DELIVERED, so `_was_delivered` cannot be what spares it.
    other = AgentEvent(kind="questions",
                       payload=json.dumps({"interrupt_id": "i-OTHER",
                                           "questions": {"name": "other"}}))
    _mark_delivered(other)
    b._queue.append(other)

    iid = json.loads(b._pending_payload)["interrupt_id"]
    # Premises: both cards are delivered, so only the id keying separates them.
    assert all(_was_delivered(e) for e in b._queue if e.kind == "questions")
    assert iid != "i-OTHER"

    assert await b.submit_answers(iid, {"1": "A"}) is True

    remaining = [_iid(e) for e in b._queue if e.kind == "questions"]
    assert remaining == ["i-OTHER"], remaining


async def test_answering_a_LIVE_turn_does_not_retire_its_undelivered_card(
        tmp_path):
    """The `_turn_active` guard on `_drop_answered_question_card`.

    This is where the port had to DIVERGE from claude_driver rather than copy it,
    and copying it verbatim broke `test_question_roundtrip` (which was right).
    Discovery's `_pump` terminates on a `questions` event, so its drop can never
    race a live generator. builder.py keeps ONE stream open across the answer
    round trip, so the normal case is a live `run()` that has not relayed the card
    yet -- answers can arrive that fast.

    Dropping unconditionally there retires the card out from under the live
    generator and the user never sees the question at all (measured: the consumer
    received `['done']` and the question silently vanished). Asserted on what the
    consumer received, which is the only place the difference shows.
    """
    import json

    holder = {}
    b = _builder(tmp_path, QuestionThenHang(lambda: holder["b"]))
    holder["b"] = b

    async def consume():
        return [ev async for ev in b.run("build")]

    turn = asyncio.create_task(consume())
    await _await_pending(b)
    # Premise: a LIVE turn, and the card has NOT reached the consumer yet.
    assert b._turn_active is True
    assert [e.kind for e in b._queue] == ["questions"]

    assert await b.submit_answers(
        json.loads(b._pending_payload)["interrupt_id"], {"1": "A"}) is True
    events = await turn

    kinds = [e.kind for e in events]
    assert "questions" in kinds, kinds     # the user must still SEE the question
    assert kinds[-1] == "done", kinds


async def test_a_DELIVERED_card_is_retired_even_though_the_turn_is_still_active(
        tmp_path):
    """Round 3: the discriminator is DELIVERY, not turn liveness.

    Round 2 used `_turn_active`, which cannot separate the two cases -- BOTH have
    it True at answer time:

      - live `run()` that has not relayed the card yet -> must NOT drop
        (test_answering_a_LIVE_turn_does_not_retire_its_undelivered_card, and
        the pre-existing test_question_roundtrip).
      - card already DELIVERED, stream then died before the next `__anext__`
        -> must drop, or it is re-shown and 409s.

    This is the second case, and it needs no exotic timing: sse_starlette hands
    the frame to the client and then awaits the network write; if the client is
    gone the task dies at that await, leaving this generator suspended at the
    `yield` with the card delivered but never popped. Note `_turn_active` is
    still True here -- that is the whole point.

    Asserted on what TURN 2's consumer receives, which is where the difference
    actually shows; asserting on builder end state is what let `_turn_active`
    look correct.
    """
    import json

    holder = {}
    b = _builder(tmp_path, DetachedQuestionClient(lambda: holder["b"]))
    holder["b"] = b

    delivered_to_client = []
    stalled = asyncio.Event()

    async def sse_like():
        """routes/prototypes.py's gen() + sse_starlette's write loop."""
        async for ev in b.run("go"):
            delivered_to_client.append(ev)
            if ev.kind == "questions":
                await stalled.wait()          # network write never completes

    task = asyncio.ensure_future(sse_like())
    for _ in range(300):
        await asyncio.sleep(0.01)
        if delivered_to_client:
            break
    assert [e.kind for e in delivered_to_client] == ["questions"]   # premise: SEEN
    task.cancel()                                                  # client gone
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Premises that make this the case round 2 got wrong.
    assert b._turn_active is True, "premise: turn liveness cannot discriminate"
    assert any(e.kind == "questions" for e in b._queue), "premise: still owned"

    assert await b.submit_answers(
        json.loads(b._pending_payload)["interrupt_id"], {"1": "A"}) is True
    assert not any(e.kind == "questions" for e in b._queue)

    # The generator is abandoned; let it finalize so a retry is admitted, exactly
    # as a reconnecting browser does.
    import gc
    del task
    gc.collect()
    for _ in range(20):
        await asyncio.sleep(0.02)
        if not b._turn_active:
            break

    turn2 = [ev async for ev in b.run("again")]
    kinds = [e.kind for e in turn2]
    assert "questions" not in kinds, kinds      # no already-answered card
    assert kinds[-1] == "done", kinds


async def test_the_identity_guard_also_protects_the_answers_drop_path(tmp_path):
    """`_drop_answered_question_card` is the SECOND head-mutating producer.

    Round 2 (`eeda393`) added it -- round 3 only changed its discriminator -- and
    it runs from `POST /answers`, so the out-of-band queue-rewrite window the
    identity guard exists for is reachable on the ordinary answer path, not only
    on a stop. Same shape as the interrupt test:
    delivered card at `queue[0]`, real work queued behind it, the drop removes
    the head and shifts that work into slot 0 while the relay is parked.
    """
    import json

    holder = {}
    b = _builder(tmp_path, DetachedQuestionClient(lambda: holder["b"]))
    holder["b"] = b

    received = []
    agen = b.run("go").__aiter__()
    while True:
        ev = await agen.__anext__()
        received.append(ev)
        if ev.kind == "questions":
            break
    await _queue_writes(b, "realwork.js")
    assert [e.kind for e in b._queue] == ["questions", "file_changed"]

    # POST /answers lands while the relay is suspended -> rewrites the queue.
    assert await b.submit_answers(
        json.loads(b._pending_payload)["interrupt_id"], {"1": "A"}) is True
    assert [e.path for e in b._queue] == ["prototype/realwork.js"], \
        "premise: the drop must have shifted realwork.js into slot 0"

    nxt = asyncio.ensure_future(agen.__anext__())
    await asyncio.sleep(0.3)
    if nxt.done():
        received.append(nxt.result())
    else:
        nxt.cancel()
        try:
            await nxt
        except asyncio.CancelledError:
            pass

    paths = [e.path for e in received if e.kind == "file_changed"]
    assert "prototype/realwork.js" in paths, [
        (e.kind, e.path or e.text) for e in received]


def test_the_two_facts_that_keep_the_mirror_window_unreachable(tmp_path):
    """Guards the reasoning in `_relay_queue`'s MIRROR WINDOW comment.

    The optimistic delivery mark has a real failure case: if the consumer is
    cancelled at its `__anext__` in the same tick the generator produced the
    value, asyncio discards the value while the event stays marked -- and a later
    answer then destroys a card the user never saw, with no re-fetch to recover
    it. Verified through the real `PrototypeSession`.

    It is unreachable from the UI only because of two facts, and the comment says
    both must stay true. This test is what makes "must stay true" enforceable
    instead of aspirational: adding a pending endpoint to the prototype path, or a
    second source for `pendingQuestions`, is a KNOWN TRIGGER and should fail here
    first rather than silently arming the window.

    Deliberately asserts on the ROUTE TABLE and the hook source text, because
    those are the two things whose change would arm it -- there is no runtime
    behavior to observe on a path that cannot currently be driven.
    """
    from pathlib import Path

    # Fact 1: no `pending` route on the prototype path. Discovery has one
    # (routes/turns.py) -- this router deliberately does not.
    from aipds.routes import prototypes as proto_routes

    proto_paths = [r.path for r in proto_routes.router.routes]
    assert not [p for p in proto_paths if "pending" in p], proto_paths

    # Fact 2: usePrototypeStream populates pendingQuestions ONLY from the SSE
    # `questions` event. Every other mention must be a CLEAR (set to null).
    #
    # Counts REFERENCES to the setter rather than matching a source line, because
    # the line-matching version was brittle in both directions (measured): a
    # behavior-identical brace reflow failed it, while a genuine second populate
    # written as `.then(setPendingQuestions)` -- the setter passed as a callback,
    # so the literal `setPendingQuestions(` never appears -- passed it.
    #
    # Every reference is counted, then the known-good ones are subtracted:
    #   - the `useState` declaration,
    #   - `setPendingQuestions(null)` clears (any number; they cannot arm this),
    #   - exactly ONE populate, inside the `questions` branch.
    # Anything left over is an unaccounted-for use, including a bare callback
    # reference. That makes the check robust to formatting while still catching
    # the aliased/callback form.
    hook = (Path(__file__).resolve().parents[2]
            / "frontend" / "lib" / "usePrototypeStream.ts")
    if not hook.exists():                       # backend-only checkout
        import pytest
        pytest.skip("frontend not present in this checkout")
    text = hook.read_text(encoding="utf-8")

    total = text.count("setPendingQuestions")
    declarations = text.count("] = useState<QuestionsPayload | null>")
    clears = text.count("setPendingQuestions(null)")
    populates = text.count("setPendingQuestions(parsed)")

    assert declarations == 1, f"expected one useState declaration, got {declarations}"
    assert populates == 1, (
        f"expected exactly ONE populate of pendingQuestions, found {populates} -- "
        "a second populate source arms the mirror window; see _relay_queue")
    unaccounted = total - declarations - clears - populates
    assert unaccounted == 0, (
        f"{unaccounted} unaccounted-for reference(s) to setPendingQuestions. A "
        "reference that is neither the declaration, a null-clear, nor the single "
        "`questions`-branch populate (e.g. `.then(setPendingQuestions)`) is a new "
        "populate source and arms the mirror window; see _relay_queue's comment.")
    # The one populate really is inside the SSE `questions` branch.
    assert 'if (ev.kind === "questions")' in text
    questions_branch = text.split('if (ev.kind === "questions")', 1)[1][:300]
    assert "setPendingQuestions(parsed)" in questions_branch


def _iid(event):
    import json
    return json.loads(event.payload)["interrupt_id"]
