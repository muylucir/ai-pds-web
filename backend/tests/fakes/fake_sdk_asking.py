# backend/tests/fakes/fake_sdk_asking.py — the ONE real-SDK behaviour
# tests/fakes/fake_sdk.py's FakeSdkClient does not have, plus the scripted-dict
# → SDK-client translation both ClaudeDriver test modules need.
#
# Why this file exists at all: `fake_sdk.FakeSdkClient` is deliberately NOT
# extended (builder tests depend on its exact `script`-of-SDK-message-objects
# shape), and BOTH tests/test_claude_driver.py and
# tests/test_claude_driver_contract.py need the same translation — so it lives
# here rather than being duplicated in each.
#
# The behaviour FakeSdkClient lacks, and why it is load-bearing:
#
#   In the real SDK an AskUserQuestion permission check arrives as a
#   `control_request` and is dispatched on a task owned by the CLIENT, not by
#   the message iterator (claude_agent_sdk/_internal/query.py:236-246,
#   `_spawn_control_request_handler` → `_handle_control_request` →
#   `await self.can_use_tool(...)`, query.py:384-412). Two consequences the
#   driver depends on, and a fake that just scripts an `AskUserQuestion`
#   ToolUseBlock reproduces neither:
#
#   1. While that task is outstanding the CLI is blocked waiting for the
#      permission response, so `receive_response()` yields NOTHING AT ALL.
#      That empty window is exactly what proto/builder.py's queue-polling loop
#      was written for: a plain `async for` never lets the queued `questions`
#      event reach the stream.
#   2. The callback OUTLIVES the message iterator. Ending the turn on a
#      question (which ClaudeDriver does) does not cancel the parked callback,
#      so the CLI can still deliver the rest of the turn afterwards. A fake
#      that cancelled the callback along with the iterator would make the
#      working design look broken.
#
#      READ THIS BEFORE CHANGING ANYTHING HERE. An earlier version of this
#      comment said the round trip works because the driver re-enters with a
#      FRESH `receive_response()` and anyio has BUFFERED what arrived
#      meanwhile. That model is FALSE, and believing it produced a
#      lost-message defect in three consecutive review rounds:
#
#        - `send_nowait` hands an item DIRECTLY to a parked receiver and does
#          NOT buffer it (anyio/streams/memory.py:220-231). While the driver is
#          parked on a receive, the item's only copy is in that receiver.
#        - Cancelling that receive therefore DESTROYS the item; a later
#          iterator finds nothing. Measured: `(parked, buffered)` goes
#          `(1, 0) -> (0, 0)` on the send, and a fresh iterator then times out.
#        - Re-arming is not a workaround either: cancelling `agen.__anext__()`
#          CLOSES the async generator, so the same iterator raises
#          StopAsyncIteration afterwards.
#
#      What the shipped driver actually does is never abandon the iterator at
#      all: `_MessageReader` opens exactly ONE `receive_response()` per turn
#      (claude_driver.py:824), keeps reading across the whole question round
#      trip, and collects into a plain list the DRIVER owns; the answers turn
#      reuses that same reader (`_continue_after_answers`) rather than opening
#      a second iterator. Buffering is ours, not anyio's — which is why this
#      fake models delivery on a REAL anyio stream (see `_stream`): only that
#      can tell a correct driver from one that loses messages.
from __future__ import annotations

import asyncio
import json

import anyio

from tests.fakes.fake_sdk import (
    AssistantMessage, FakeSdkClient, ResultMessage, TextBlock, ToolUseBlock,
)

# Every AskingSdkClient built during a test, so a fixture can cancel a
# still-parked callback task at teardown. Without that, a question left
# unanswered at the end of a test reaches loop close as a pending task and
# asyncio logs "Task was destroyed but it is pending!" — noise that would
# train us to ignore that message, which is a real leak signal elsewhere.
_LIVE: list["AskingSdkClient"] = []


def cancel_pending_callbacks() -> None:
    for client in _LIVE:
        client.cancel_pending()
    _LIVE.clear()


# The SDK-shaped AskUserQuestion input the driver's callback receives. Two
# options with descriptions, so question_file_from_sdk's label/description
# merge and its letter indexing both get exercised.
DEFAULT_SDK_QUESTIONS = [{
    "question": "다음 단계는?", "header": "Next", "multiSelect": False,
    "options": [{"label": "진행", "description": "계속"},
                {"label": "종료", "description": "핸드오프"}],
}]

# The model's "why I'm asking" prose that precedes a question in real turns.
PREFACE_TEXT = "이 질문이 왜 필요한지 먼저 설명합니다."

# A DIFFERENT question for the follow-up-during-an-answers-turn script, so a
# driver that somehow replayed the first question would not look correct.
FOLLOWUP_SDK_QUESTIONS = [{
    "question": "추가로 필요한 정보는?", "header": "Followup",
    "multiSelect": False,
    "options": [{"label": "예", "description": "있음"},
                {"label": "아니오", "description": "없음"}],
}]


def script_from(scripted: dict) -> list:
    """Contract-test dict script → the SDK message-object list FakeSdkClient
    eats. `questions`/`followup_questions` are deliberately NOT represented
    here — those go through AskingSdkClient's permission callback, which is
    the only path that produces a `questions` event for real.

    `result_is_error`/`result_terminal_reason` script the turn's closing
    ResultMessage directly — the CLI reports both a genuine failure (Bedrock
    429/500/529, a wedged tool) and an interrupted turn this way, and the two
    are told apart only by `terminal_reason` (SDK types.py:1249-1257)."""
    blocks: list = [TextBlock(text=t) for t in scripted.get("text", [])]
    blocks += [ToolUseBlock(id=f"t{i}", name=n, input={})
               for i, n in enumerate(scripted.get("tools", []))]
    msgs: list = []
    if blocks:
        msgs.append(AssistantMessage(content=blocks))
    msgs.append(ResultMessage(
        is_error=bool(scripted.get("result_is_error", False)),
        terminal_reason=scripted.get("result_terminal_reason")))
    return msgs


class AskingSdkClient(FakeSdkClient):
    """Drives the driver's `can_use_tool` callback the way the real SDK does.

    The callback is spawned on a task this CLIENT owns (not the iterator's),
    and `receive_response()` yields nothing until it finishes — reproducing
    both halves of the real behaviour described in the module docstring.

    `permission_results` records what the callback returned, so a test can
    assert on the answers the driver injected as `updated_input` rather than
    just on the event stream.

    `preface` — one or more `{"type":"assistant"}` messages (the model's "why
    I'm asking" prose plus the AskUserQuestion tool_use) delivered back to back
    with the `control_request` in ONE read-loop pass (query.py:250-322), no model
    latency between them. Pass a str for one message or a list for several.
    SEVERAL is the case that matters most: a driver that consumes one message
    per poll pass and then discards its in-flight receive keeps only the FIRST.
    Real inter-message gaps were measured at 3-4ms — far inside the driver's 50ms
    poll — so multiple messages in one pass is routine, not hypothetical.

    Without it the callback is spawned before anything is yielded, which is why
    the original question scripts never had a message in flight and never caught
    any of this.

    For a message that must arrive at some LATER, precisely chosen instant, the
    test calls `deliver_late()` from inside its own `async for` — at that point
    the driver is provably suspended at that exact `yield`, so no window-guessing
    is involved. A previous version of this fake tried to find such windows by
    polling driver state, and that predicate had false positives in both
    directions (it was True during `_on_can_use_tool`'s S3 save and True after
    the drain had finished), so a test could go vacuous while still reporting a
    hit. See test_claude_driver.py's mid-turn delivery tests.
    """

    def __init__(self, can_use_tool, *, sdk_questions=None, tail=None,
                 preface=None, result_with_question=False):
        super().__init__(tail if tail is not None else [ResultMessage()])
        self._can_use_tool = can_use_tool
        self._sdk_questions = (DEFAULT_SDK_QUESTIONS if sdk_questions is None
                               else sdk_questions)
        if preface is None:
            self._preface: list[str] = []
        elif isinstance(preface, str):
            self._preface = [preface]
        else:
            self._preface = list(preface)
        self._result_with_question = result_with_question
        self._send = None
        self._recv = None
        self._ask_task: asyncio.Task | None = None
        self.permission_results: list = []
        _LIVE.append(self)

    def cancel_pending(self) -> None:
        if self._ask_task is not None and not self._ask_task.done():
            self._ask_task.cancel()

    def _stream(self):
        """Lazily create the anyio memory-object stream messages travel on.

        A real anyio stream, not a list, because the whole bug class this fake
        exists to catch lives in anyio's delivery semantics: `send_nowait` hands
        an item to a PARKED receiver and bypasses the buffer, so cancelling that
        receiver destroys the item, while an item sent with no receiver parked is
        buffered and survives. A list-based fake cannot exhibit either behaviour,
        so it cannot tell a correct driver from one that loses messages. The real
        SDK owns exactly such a stream on the CLIENT (query.py:121), which is
        also why abandoning one `receive_response()` does not lose what the next
        one will read.
        """
        if self._send is None:
            self._send, self._recv = anyio.create_memory_object_stream(
                max_buffer_size=100)
        return self._send

    def deliver_late(self, text: str) -> None:
        """Hand over one more assistant message right now.

        For modelling what the CLI does while the consumer is away — e.g.
        suspended on `yield done` awaiting an S3 workspace sync, or gone
        entirely after an SSE disconnect. Called from a test's own `async for`
        body, so the driver's position is known exactly rather than guessed."""
        self._stream().send_nowait(
            AssistantMessage(content=[TextBlock(text=text)]))

    def finish_turn(self) -> None:
        """End the turn from the CLI side, without a permission answer.

        Models the turn running to completion while the consumer is away, which
        is what lets a test check that a message delivered mid-abandonment is
        relayed by the answers turn AND that the answers turn still terminates.
        """
        self._stream().send_nowait(ResultMessage())

    def stream_stats(self) -> tuple[int, int]:
        """(parked receivers, buffered items) — lets a test assert on WHERE a
        message went, not just whether it eventually appeared."""
        self._stream()
        state = self._recv._state
        return len(state.waiting_receivers), len(state.buffer)

    def _produce(self) -> None:
        """The CLI's read loop: everything the question burst carries, at once.

        Synchronous, and called before the first receive parks, because that is
        what the real read loop does — the assistant message(s) and the
        `control_request` go out in one pass (query.py:250-322) with no model
        latency in between. Anything that has to arrive LATER is delivered by the
        test through `deliver_late()`, from a point where the driver's position
        is known rather than guessed.

        Note there is no "yield nothing while the question is pending" special
        case: that behaviour falls out for free, because while the CLI is blocked
        awaiting the permission response it simply sends nothing, and the
        consumer's receive parks. Which is precisely the situation the driver has
        to survive."""
        send = self._stream()
        for text in self._preface:
            send.send_nowait(AssistantMessage(content=[TextBlock(text=text)]))
        if self._result_with_question:
            # The turn's ResultMessage in the SAME burst as the question, so the
            # driver's terminal harvest translates it -- the shape that produced
            # a duplicate `done`.
            send.send_nowait(ResultMessage())

    def _on_permission_result(self, task: asyncio.Future) -> None:
        """Permission granted -> the CLI resumes the turn and finishes it."""
        if task.cancelled():
            return
        if task.exception() is not None:  # pragma: no cover — defensive
            return
        self.permission_results.append(task.result())
        for msg in self.script:
            self._stream().send_nowait(msg)

    async def receive_response(self):
        if self._ask_task is None:
            self._stream()
            # Spawned with NO await before the preface is sent, so the assistant
            # messages and the permission request land in the same tick exactly
            # as the CLI's read loop delivers them.
            self._ask_task = asyncio.ensure_future(self._can_use_tool(
                "AskUserQuestion", {"questions": self._sdk_questions}, None))
            self._ask_task.add_done_callback(self._on_permission_result)
            self._produce()
        # Awaiting the stream directly is what parks an anyio receiver — the
        # behaviour under test. A second receive_response() over the same turn
        # picks up wherever this one left off, exactly as the SDK's does.
        async for msg in self._recv:
            yield msg
            if isinstance(msg, ResultMessage):
                return

    async def disconnect(self):
        # The real SDK cancels in-flight control requests on close
        # (query.py `_close_impl`: `for task in self._child_tasks: cancel()`).
        self.cancel_pending()
        await super().disconnect()


class RaisingSdkClient(FakeSdkClient):
    """SDK exception path — the contract requires it degrade to
    "agent turn failed"."""

    async def receive_response(self):
        raise RuntimeError("boom")
        yield  # pragma: no cover — makes this an async generator


class SessionIdCheckingSdkClient(FakeSdkClient):
    """Enforces the CLI's `--session-id` / `--resume` rules inside connect().

    `FakeSdkClient.connect()` is a no-op, which is exactly why the whole
    session-id defect class was invisible to 624 green tests: the ONE thing the
    real CLI does here — refuse the flag combination — was the one thing no fake
    modelled. A fake that cannot reject a duplicate session id cannot prove a
    driver got this right.

    Both rules are measured against the bundled binary (2.1.220), and they are
    exact complements, so a driver that hardcodes EITHER flag fails one of them:

        --session-id=<id>, transcript exists  -> exit 1, "Session ID <id> is
                                                 already in use."
        --resume=<id>,     transcript absent  -> exit 1, "No conversation found
                                                 with session ID: <id>"

    The truth this checks against is a REAL FILE on disk, at the same path the
    CLI uses (`<config_dir>/projects/<encoded cwd>/<id>.jsonl`, every character
    outside [A-Za-z0-9-] replaced by "-"), because that file's existence IS the
    CLI's check: probed by moving one .jsonl aside, after which the same
    `--session-id` that had just been refused succeeded again. Modelling it as a
    filesystem fact rather than an in-fake flag is what makes the driver's own
    probe (`_transcript_exists`) genuinely tested instead of mirrored.

    A successful connect CREATES the transcript, so a second client for the same
    (cwd, id) — a restart, or a project deleted and re-created — is in the state
    the CLI would really be in. The error is the SDK's own ProcessError, the
    type the real failure arrives as (measured end-to-end: connect() raises
    ProcessError, not a CLI string).
    """

    def __init__(self, session: dict, config_dir: str, workspace: str,
                 script=None):
        super().__init__(script if script is not None else [ResultMessage()])
        self._session = session
        self._config_dir = config_dir
        self._workspace = workspace

    def _transcript(self):
        # Imported from the driver on purpose: the fake must look where the
        # DRIVER thinks the CLI looks, so a wrong path in the driver shows up
        # as a failing test rather than as two consistent mistakes. The path
        # builder itself is pinned to the real CLI by its own unit tests.
        from pathfinder.agent.claude_driver import _sdk_session_id, _transcript_path
        session_id, _ = _sdk_session_id(self._session)
        return _transcript_path(self._config_dir, self._workspace, session_id)

    async def connect(self):
        from claude_agent_sdk._errors import ProcessError
        path = self._transcript()
        resume = bool(self._session.get("resume"))
        if resume and not path.is_file():
            raise ProcessError("Command failed with exit code 1", exit_code=1,
                               stderr=f"No conversation found with session ID: {path.stem}")
        if not resume and path.is_file():
            raise ProcessError("Command failed with exit code 1", exit_code=1,
                               stderr=f"Session ID {path.stem} is already in use.")
        # A real session leaves its transcript behind, and it OUTLIVES the
        # process — which is what makes the collision permanent rather than
        # self-healing.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        await super().connect()


class EchoAnswersSdkClient(FakeSdkClient):
    """`echo_answers`: echo back the interrupt_id/answers this fake ACTUALLY
    received, so the contract can verify run_answers forwarded the caller's
    values rather than dropping them.

    Nothing is hardcoded here: both values are read out of the last prompt the
    driver passed to `query()`, whose final line is the machine-readable
    answer record ClaudeDriver._resume_with_answers appends (see that method
    for why the record is in the prompt at all). A driver that dropped or
    mangled either value yields a mismatch or a JSONDecodeError, not a pass.
    """

    async def receive_response(self):
        prompt = self.queries[-1] if self.queries else ""
        record = json.loads(prompt.rsplit("\n", 1)[-1])
        yield AssistantMessage(content=[TextBlock(text=json.dumps({
            "interrupt_id": record["interrupt_id"],
            "answers": record["answers"],
        }, ensure_ascii=False))])
        yield ResultMessage()


def sdk_client_for(scripted: dict, can_use_tool):
    """The full six-key scripted vocabulary → one fake SDK client.

    `can_use_tool` is the driver's own `_on_can_use_tool`, wired in at exactly
    the place the real factory wires it (`ClaudeAgentOptions(can_use_tool=)`).
    """
    if scripted.get("raise"):
        return RaisingSdkClient()
    if scripted.get("echo_answers"):
        return EchoAnswersSdkClient()
    if scripted.get("followup_questions"):
        return AskingSdkClient(can_use_tool,
                               sdk_questions=FOLLOWUP_SDK_QUESTIONS)
    if scripted.get("questions"):
        # `preface` on by default: the real CLI always emits the model's
        # explanation immediately before the question (driver.py's
        # _CONTACT_ADDENDUM:44-45 mandates it), so the default script must too.
        # `preface_texts` lets a driver-specific test put several messages in one
        # read-loop pass without changing what the shared contract script does.
        # `turn_continues_after_answer`: answering sends NOTHING, modelling the
        # normal case where the model keeps working for seconds after the answer
        # before the CLI emits the turn's ResultMessage. The test then supplies
        # the rest itself with deliver_late()/finish_turn(). Without this the
        # tail's ResultMessage lands the instant the answer does, which ends the
        # turn far earlier than any real one.
        tail = [] if scripted.get("turn_continues_after_answer") \
            else script_from(scripted)
        return AskingSdkClient(
            can_use_tool, tail=tail,
            preface=scripted.get("preface_texts") or PREFACE_TEXT,
            result_with_question=bool(scripted.get("result_with_question")))
    return FakeSdkClient(script_from(scripted))
