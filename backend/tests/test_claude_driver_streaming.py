# backend/tests/test_claude_driver_streaming.py — token delta translation and thinking-span signals.
#
# Measured (2026-09-04, Bedrock · global.anthropic.claude-opus-5): with partial messages on,
# assistant text arrives as 295 `text_delta`s (1,325 characters), and **the complete
# `AssistantMessage` arrives again after them.** And even with `--thinking-display summarized`,
# `thinking_delta` is 0 characters (only the signature comes) — that is, thinking **text** does not
# exist on this path, and what we get is the start and end of the thinking span.
import pytest

from aipds.agent.claude_driver import (
    THINKING_DONE_MARKER, THINKING_MARKER, ClaudeDriver,
)
from tests.fakes.fake_sdk import (
    AssistantMessage, StreamEvent, TextBlock, ToolUseBlock,
)
from tests.fakes.fake_sdk_asking import cancel_pending_callbacks, sdk_client_for
from tests.fakes.in_memory_s3 import FakeS3Store


@pytest.fixture(autouse=True)
def _cleanup_parked_callbacks():
    yield
    cancel_pending_callbacks()


def _driver(tmp_path) -> ClaudeDriver:
    """A driver that only looks at translation. It does not use a client, so there is no script."""
    return ClaudeDriver(workspace=str(tmp_path), rules_dir=str(tmp_path),
                        config_dir=str(tmp_path / "cfg"), s3=FakeS3Store(),
                        client_factory=lambda session: None)


def _scripted_driver(tmp_path, scripted) -> ClaudeDriver:
    """A driver that actually runs one turn (same wiring as `_driver` in test_claude_driver)."""
    rules = tmp_path / "rules" / "aws-aiplc-rules"
    rules.mkdir(parents=True)
    (rules / "core-workflow.md").write_text("WORKFLOW", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    d = ClaudeDriver(workspace=str(ws), rules_dir=str(tmp_path / "rules"),
                     config_dir=str(tmp_path / "cfg"), s3=FakeS3Store(),
                     client_factory=lambda session: None)
    d._client_factory = lambda session: sdk_client_for(  # type: ignore[assignment]
        scripted, d._on_can_use_tool)
    return d


def _delta(text: str) -> StreamEvent:
    return StreamEvent({"type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": text}})


def _texts(events) -> list[str]:
    return [e.text for e in events if e.kind == "message"]


def test_text_deltas_become_message_events_up_to_a_whitespace_boundary(tmp_path):
    d = _driver(tmp_path)
    assert _texts(d._translate(_delta("안녕하"))) == []
    assert _texts(d._translate(_delta("세요 반갑"))) == ["안녕하세요 "]


def test_block_stop_releases_the_held_tail(tmp_path):
    """The last token has no trailing whitespace. If it is not emitted when the block ends,
    the end of the sentence is lost forever."""
    d = _driver(tmp_path)
    d._translate(_delta("끝말"))
    stop = d._translate(StreamEvent({"type": "content_block_stop"}))
    assert _texts(stop) == ["끝말"]


def test_streamed_text_is_not_repeated_by_the_assistant_message(tmp_path):
    """Even with partial messages on, the complete AssistantMessage follows (the message flow in
    the docs; in measurement too, 2 AssistantMessages). Translating that TextBlock as is would
    render every paragraph on screen twice."""
    d = _driver(tmp_path)
    d._translate(_delta("본문 "))
    later = d._translate(AssistantMessage(content=[TextBlock(text="본문 ")]))
    assert _texts(later) == []


def test_a_message_that_never_streamed_still_yields_its_text(tmp_path):
    """Paths with no deltas (sessions without partial streaming, scripted fakes) must behave as
    before — applying dedup unconditionally would make the answers on that path disappear."""
    d = _driver(tmp_path)
    events = d._translate(AssistantMessage(content=[TextBlock(text="델타 없음")]))
    assert _texts(events) == ["델타 없음"]


def test_the_next_message_streams_again_after_one_was_deduped(tmp_path):
    """The duplicate judgment is **per message**. If the fact that a delta was seen once carries over
    to the next message, a second answer that came without deltas silently disappears."""
    d = _driver(tmp_path)
    d._translate(_delta("첫 "))
    d._translate(AssistantMessage(content=[TextBlock(text="첫 ")]))
    second = d._translate(AssistantMessage(content=[TextBlock(text="둘째")]))
    assert _texts(second) == ["둘째"]


def test_the_assistant_message_flushes_a_tail_that_block_stop_never_released(tmp_path):
    """If a message ends without content_block_stop being seen, the tail is trapped in the buffer.
    Skipping the TextBlock too in that state loses text — it is lost on neither side."""
    d = _driver(tmp_path)
    d._translate(_delta("꼬리"))
    later = d._translate(AssistantMessage(content=[TextBlock(text="꼬리")]))
    assert _texts(later) == ["꼬리"]


def test_tool_use_still_reports_status_after_text_streamed(tmp_path):
    """Dedup concerns only TextBlocks. Tool traces must keep coming."""
    d = _driver(tmp_path)
    d._translate(_delta("읽어볼게 "))
    events = d._translate(AssistantMessage(
        content=[ToolUseBlock(id="t1", name="Read", input={"file_path": "a.md"})]))
    assert [e.text for e in events if e.kind == "status"] == ["Read"]


def test_thinking_block_start_and_stop_become_status_markers(tmp_path):
    """Thinking text is not on this path, but the span is. Right now ActivityIndicator guesses
    "if there is no tool, it is thinking", and this signal turns that guess into fact. It is a
    status marker rather than a new event kind for the same reason as interrupt's
    INTERRUPTED_MARKER — it is a live-only signal, so it does not touch the history
    contract."""
    d = _driver(tmp_path)
    start = d._translate(StreamEvent({
        "type": "content_block_start",
        "content_block": {"type": "thinking"}}))
    assert [e.text for e in start if e.kind == "status"] == [THINKING_MARKER]
    stop = d._translate(StreamEvent({"type": "content_block_stop"}))
    assert [e.text for e in stop if e.kind == "status"] == [THINKING_DONE_MARKER]


def test_a_text_block_stop_does_not_report_thinking_done(tmp_path):
    """content_block_stop arrives for both thinking blocks and text blocks. Without distinguishing,
    the done marker would go out even on turns with no thinking, and the frontend would turn off a state that was never on."""
    d = _driver(tmp_path)
    d._translate(StreamEvent({"type": "content_block_start",
                              "content_block": {"type": "text"}}))
    stop = d._translate(StreamEvent({"type": "content_block_stop"}))
    assert [e.text for e in stop if e.kind == "status"] == []


def test_thinking_deltas_do_not_leak_into_the_answer(tmp_path):
    """Putting thinking deltas into the body buffer would mix the model's reasoning into the answer. On
    this path the content is empty (measured 0 characters), but this locks it so it does not silently mix in the day values appear."""
    d = _driver(tmp_path)
    events = d._translate(StreamEvent({
        "type": "content_block_delta",
        "delta": {"type": "thinking_delta", "thinking": "속으로 생각한 내용"}}))
    assert _texts(events) == []
    tail = d._translate(StreamEvent({"type": "content_block_stop"}))
    assert _texts(tail) == []


def test_signature_delta_is_ignored(tmp_path):
    """A frame that comes with thinking blocks in measurement. It is neither body nor trace."""
    d = _driver(tmp_path)
    events = d._translate(StreamEvent({
        "type": "content_block_delta",
        "delta": {"type": "signature_delta", "signature": "abc"}}))
    assert events == []


@pytest.mark.asyncio
async def test_a_new_turn_does_not_inherit_the_previous_turns_delta_state(tmp_path):
    """Delta state lives for **the instance's lifetime** (the driver keeps handling one project).

    If a turn is abandoned midway (SSE drop, proxy timeout, page navigation — runner.py really
    has that path), `_streamed_text` stays True and a tail is trapped in the buffer. If the next
    turn starts in that state, that turn's first AssistantMessage is judged a duplicate and
    **the whole answer disappears.** It must be flushed at the turn boundary.
    """
    d = _scripted_driver(tmp_path, {"text": ["새 턴 답변"]})
    d._streamed_text = True
    d._in_thinking = True
    d._delta_buf.feed("앞턴의남은꼬리")

    texts = [e.text async for e in d.run("hi", {"session_id": "s-1"})
             if e.kind == "message"]
    assert "새 턴 답변" in texts, texts
    assert not any("앞턴의남은꼬리" in (t or "") for t in texts), texts


def test_partial_messages_are_enabled_on_the_real_options(tmp_path, monkeypatch):
    """The tests above that inject client_factory do not take this path, so they all pass even if
    the wiring is missing (same comment as in test_cli_settings)."""
    from aipds.agent.claude_driver import _default_client_factory

    captured = {}

    class FakeClient:
        def __init__(self, options=None):
            captured["options"] = options

    import claude_agent_sdk
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", FakeClient)
    driver = ClaudeDriver(workspace=str(tmp_path), rules_dir=str(tmp_path),
                          config_dir=str(tmp_path / "cfg"), s3=FakeS3Store())
    _default_client_factory(driver)({"session_id": "p1", "resume": False})
    assert captured["options"].include_partial_messages is True


# ---- Write tool input becomes draft events (agent/tool_input_draft.py) ----
# Measured 2026-10-07: about 60% of turn time was spent generating file contents, and during that
# time the screen showed no signal. tests/test_tool_input_draft.py covers the decision table; here we
# pin the **wiring**.
import json as _json  # noqa: E402


def _tool_start(index: int, name: str, tid: str) -> StreamEvent:
    return StreamEvent({"type": "content_block_start", "index": index,
                        "content_block": {"type": "tool_use", "id": tid, "name": name,
                                          "input": {}}})


def _json_delta(index: int, partial: str) -> StreamEvent:
    return StreamEvent({"type": "content_block_delta", "index": index,
                        "delta": {"type": "input_json_delta", "partial_json": partial}})


def test_a_write_block_streams_as_draft_events(tmp_path):
    d = _driver(tmp_path)
    body = "# 문서\n\n본문"
    raw = _json.dumps({"file_path": f"{tmp_path}/aiplc-docs/discovery/x.md", "content": body})
    events = d._translate(_tool_start(1, "Write", "toolu_1"))
    for i in range(0, len(raw), 4):
        events += d._translate(_json_delta(1, raw[i:i + 4]))
    events += d._translate(StreamEvent({"type": "content_block_stop", "index": 1}))

    drafts = [e for e in events if e.kind == "draft"]
    payloads = [_json.loads(e.payload) for e in drafts]
    assert payloads[0]["state"] == "writing"
    assert payloads[-1]["state"] == "written"
    assert payloads[-1]["chars"] == len(body)
    assert drafts[-1].path == "aiplc-docs/discovery/x.md"
    # A draft is not body text — if it leaked into a bubble, the document would pour into the chat.
    assert _texts(events) == []


def test_a_non_file_tool_block_is_not_a_draft(tmp_path):
    d = _driver(tmp_path)
    events = d._translate(_tool_start(0, "Bash", "toolu_2"))
    events += d._translate(_json_delta(0, '{"command": "ls"}'))
    events += d._translate(StreamEvent({"type": "content_block_stop", "index": 0}))
    assert not [e for e in events if e.kind == "draft"]


def test_text_blocks_and_drafts_do_not_cross(tmp_path):
    """Even if a text block with a different index comes between drafts, each goes its own way."""
    d = _driver(tmp_path)
    raw = _json.dumps({"file_path": f"{tmp_path}/aiplc-docs/audit.md", "content": "기록"})
    events = d._translate(_tool_start(2, "Write", "toolu_3"))
    events += d._translate(_json_delta(2, raw[:10]))
    events += d._translate(StreamEvent({"type": "content_block_delta", "index": 0,
                                        "delta": {"type": "text_delta", "text": "안내 "}}))
    events += d._translate(_json_delta(2, raw[10:]))
    events += d._translate(StreamEvent({"type": "content_block_stop", "index": 2}))
    assert _texts(events) == ["안내 "]
    assert [_json.loads(e.payload)["state"] for e in events if e.kind == "draft"][-1] == "written"
