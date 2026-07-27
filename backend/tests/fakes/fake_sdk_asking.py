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
#   2. The callback OUTLIVES the message iterator. Abandoning
#      `receive_response()` (which ClaudeDriver does when it ends the turn on
#      a question) does not cancel the parked callback, and messages buffered
#      meanwhile are not lost — the buffer belongs to Query's anyio memory
#      stream, not to the generator. That is what makes ClaudeDriver's
#      "end the turn, resolve the future later, re-enter with a FRESH
#      receive_response()" round trip legal. A fake that cancelled the
#      callback along with the iterator would make the working design look
#      broken.
from __future__ import annotations

import asyncio
import json

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
    the only path that produces a `questions` event for real."""
    blocks: list = [TextBlock(text=t) for t in scripted.get("text", [])]
    blocks += [ToolUseBlock(id=f"t{i}", name=n, input={})
               for i, n in enumerate(scripted.get("tools", []))]
    msgs: list = []
    if blocks:
        msgs.append(AssistantMessage(content=blocks))
    msgs.append(ResultMessage())
    return msgs


class AskingSdkClient(FakeSdkClient):
    """Drives the driver's `can_use_tool` callback the way the real SDK does.

    The callback is spawned on a task this CLIENT owns (not the iterator's),
    and `receive_response()` yields nothing until it finishes — reproducing
    both halves of the real behaviour described in the module docstring.

    `permission_results` records what the callback returned, so a test can
    assert on the answers the driver injected as `updated_input` rather than
    just on the event stream.

    Three knobs reproduce the message/question interleavings the real CLI
    produces. All of them exist because the driver must never discard a message
    the SDK already handed it, and each interleaving destroys it differently:

    `preface` — one or more `{"type":"assistant"}` messages (the model's "why
    I'm asking" prose plus the AskUserQuestion tool_use) delivered back to back
    with the `control_request` in ONE read-loop pass (query.py:250-322), no model
    latency between them. Pass a str for one message or a list for several.
    SEVERAL is the case that matters most: a driver whose sweep re-arms the
    receive with `ensure_future` (never synchronously `done()` on its creation
    tick) consumes only the FIRST and destroys the rest. Real inter-message gaps
    were measured at 3-4ms — far inside the driver's 50ms poll — so multiple
    messages in one pass is routine, not hypothetical.

    `during_drain` — a message delivered AFTER the question is already queued,
    timed to land while the driver is yielding its queued events. Each `yield`
    hands control to the scheduler, which is precisely the window the re-armed
    receive needs, so this message is legitimately deliverable and must not be
    dropped on the way out.

    Without any of them the callback is spawned before anything is yielded,
    which is why the original question scripts never had a message in flight
    and never caught any of this.
    """

    def __init__(self, can_use_tool, *, sdk_questions=None, tail=None,
                 preface=None, during_drain=None):
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
        self._during_drain = during_drain
        self._ask_task: asyncio.Task | None = None
        self.permission_results: list = []
        _LIVE.append(self)

    def cancel_pending(self) -> None:
        if self._ask_task is not None and not self._ask_task.done():
            self._ask_task.cancel()

    # Scheduler turns to wait before delivering the `during_drain` message.
    # Established by a turn-by-turn trace of this fake against the real driver:
    #
    #   fake: yielded <preface>
    #   fake: turn 1      <- driver yields `questions` here (drain has begun)
    #   fake: turn 2      <- deliver here: past the pre-drain sweep, so only a
    #                        POST-drain sweep can still recover this message
    #
    # Delivering any earlier is consumed by the pre-drain sweep, which is a
    # different window and leaves the post-drain sweep unexercised (verified:
    # at turn 1 the test passes even with the post-drain sweep removed).
    _DURING_DRAIN_TURNS = 2

    async def _deliver_during_drain(self) -> None:
        """Park until the driver is past its pre-drain sweep and inside the
        queue-drain loop (see _DURING_DRAIN_TURNS)."""
        for _ in range(self._DURING_DRAIN_TURNS):
            await asyncio.sleep(0)

    async def receive_response(self):
        if self._ask_task is None:
            # One handler per AskUserQuestion tool call, spawned once — a
            # second receive_response() over the same turn must not re-ask.
            # Spawned BEFORE the preface is yielded, with no await in between,
            # so the assistant messages and the permission request land in the
            # same tick exactly as the CLI's read loop delivers them.
            self._ask_task = asyncio.ensure_future(self._can_use_tool(
                "AskUserQuestion", {"questions": self._sdk_questions}, None))
            for text in self._preface:
                yield AssistantMessage(content=[TextBlock(text=text)])
            if self._during_drain is not None:
                # Must NOT be yielded inline: the driver's pre-drain sweep
                # would consume it in the same pass and the mid-drain window
                # would never be exercised. Instead park here until the driver
                # is one scheduler turn in — i.e. suspended on a `yield` inside
                # its queue-drain loop — and only then deliver.
                await self._deliver_during_drain()
                yield AssistantMessage(
                    content=[TextBlock(text=self._during_drain)])
        while not self._ask_task.done():
            # Deliberately NOT `await self._ask_task`: the point is that this
            # generator produces no messages while the permission request is
            # outstanding, which is what starves a plain `async for`. And
            # being cancelled here must NOT cancel the task (see docstring).
            await asyncio.sleep(0.01)
        if not self.permission_results:
            self.permission_results.append(self._ask_task.result())
        for msg in self.script:
            yield msg

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
        # `preface_texts`/`during_drain` let a driver-specific test script the
        # harder interleavings (several messages in one read-loop pass; a
        # message landing while the driver drains its queue) without changing
        # what the shared contract script does.
        return AskingSdkClient(
            can_use_tool, tail=script_from(scripted),
            preface=scripted.get("preface_texts") or PREFACE_TEXT,
            during_drain=scripted.get("during_drain"))
    return FakeSdkClient(script_from(scripted))
