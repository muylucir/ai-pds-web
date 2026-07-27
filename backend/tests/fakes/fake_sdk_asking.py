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
    """

    def __init__(self, can_use_tool, *, sdk_questions=None, tail=None):
        super().__init__(tail if tail is not None else [ResultMessage()])
        self._can_use_tool = can_use_tool
        self._sdk_questions = (DEFAULT_SDK_QUESTIONS if sdk_questions is None
                               else sdk_questions)
        self._ask_task: asyncio.Task | None = None
        self.permission_results: list = []
        _LIVE.append(self)

    def cancel_pending(self) -> None:
        if self._ask_task is not None and not self._ask_task.done():
            self._ask_task.cancel()

    async def receive_response(self):
        if self._ask_task is None:
            # One handler per AskUserQuestion tool call, spawned once — a
            # second receive_response() over the same turn must not re-ask.
            self._ask_task = asyncio.ensure_future(self._can_use_tool(
                "AskUserQuestion", {"questions": self._sdk_questions}, None))
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
        return AskingSdkClient(can_use_tool, tail=script_from(scripted))
    return FakeSdkClient(script_from(scripted))
