# backend/tests/test_claude_driver_streaming.py — 토큰 델타 번역과 사고 구간 신호.
#
# 실측(2026-09-04, Bedrock · global.anthropic.claude-opus-5): 부분 메시지를 켜면
# 어시스턴트 텍스트가 295개의 `text_delta`(1,325자)로 오고, **완성된
# `AssistantMessage`가 그 뒤에 또 온다.** 그리고 `--thinking-display summarized`를
# 붙여도 `thinking_delta`는 0자다(signature만 온다) — 즉 사고 **텍스트**는 이
# 경로에서 존재하지 않고, 우리가 얻는 것은 사고 구간의 시작과 끝이다.
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
    """번역만 보는 드라이버. 클라이언트를 쓰지 않으므로 대본이 없다."""
    return ClaudeDriver(workspace=str(tmp_path), rules_dir=str(tmp_path),
                        config_dir=str(tmp_path / "cfg"), s3=FakeS3Store(),
                        client_factory=lambda session: None)


def _scripted_driver(tmp_path, scripted) -> ClaudeDriver:
    """턴 하나를 실제로 도는 드라이버(test_claude_driver의 `_driver`와 같은 배선)."""
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
    """마지막 토큰에는 뒤따르는 공백이 없다. 블록이 끝날 때 내보내지 않으면
    문장의 끝이 영구히 사라진다."""
    d = _driver(tmp_path)
    d._translate(_delta("끝말"))
    stop = d._translate(StreamEvent({"type": "content_block_stop"}))
    assert _texts(stop) == ["끝말"]


def test_streamed_text_is_not_repeated_by_the_assistant_message(tmp_path):
    """부분 메시지를 켜도 완성된 AssistantMessage가 뒤따라 온다(문서의 메시지
    흐름, 실측에서도 AssistantMessage 2개). 그 TextBlock을 그대로 번역하면
    모든 문단이 화면에 두 번 렌더된다."""
    d = _driver(tmp_path)
    d._translate(_delta("본문 "))
    later = d._translate(AssistantMessage(content=[TextBlock(text="본문 ")]))
    assert _texts(later) == []


def test_a_message_that_never_streamed_still_yields_its_text(tmp_path):
    """델타가 없는 경로(부분 스트리밍이 없는 세션, 스크립트된 가짜)는 종전대로
    동작해야 한다 — 중복 제거를 무조건 적용하면 그 경로의 답변이 사라진다."""
    d = _driver(tmp_path)
    events = d._translate(AssistantMessage(content=[TextBlock(text="델타 없음")]))
    assert _texts(events) == ["델타 없음"]


def test_the_next_message_streams_again_after_one_was_deduped(tmp_path):
    """중복 판정은 **메시지 단위**다. 한 번 델타를 봤다는 사실이 다음 메시지까지
    남으면, 델타 없이 온 두 번째 답변이 조용히 사라진다."""
    d = _driver(tmp_path)
    d._translate(_delta("첫 "))
    d._translate(AssistantMessage(content=[TextBlock(text="첫 ")]))
    second = d._translate(AssistantMessage(content=[TextBlock(text="둘째")]))
    assert _texts(second) == ["둘째"]


def test_the_assistant_message_flushes_a_tail_that_block_stop_never_released(tmp_path):
    """content_block_stop을 못 본 채 메시지가 끝나면 꼬리가 버퍼에 갇힌다.
    그 상태로 TextBlock까지 건너뛰면 텍스트를 잃는다 — 어느 쪽으로도 잃지 않는다."""
    d = _driver(tmp_path)
    d._translate(_delta("꼬리"))
    later = d._translate(AssistantMessage(content=[TextBlock(text="꼬리")]))
    assert _texts(later) == ["꼬리"]


def test_tool_use_still_reports_status_after_text_streamed(tmp_path):
    """중복 제거는 TextBlock만의 이야기다. 도구 트레이스는 계속 나와야 한다."""
    d = _driver(tmp_path)
    d._translate(_delta("읽어볼게 "))
    events = d._translate(AssistantMessage(
        content=[ToolUseBlock(id="t1", name="Read", input={"file_path": "a.md"})]))
    assert [e.text for e in events if e.kind == "status"] == ["Read"]


def test_thinking_block_start_and_stop_become_status_markers(tmp_path):
    """사고 텍스트는 이 경로에 없지만 구간은 있다. 지금 ActivityIndicator는
    "도구가 없으면 생각하는 중"이라고 추측하는데, 이 신호가 그 추측을 사실로
    바꾼다. 새 이벤트 kind가 아니라 status 마커인 이유는 interrupt의
    INTERRUPTED_MARKER와 같다 — 라이브 전용 신호이므로 히스토리 계약을
    건드리지 않는다."""
    d = _driver(tmp_path)
    start = d._translate(StreamEvent({
        "type": "content_block_start",
        "content_block": {"type": "thinking"}}))
    assert [e.text for e in start if e.kind == "status"] == [THINKING_MARKER]
    stop = d._translate(StreamEvent({"type": "content_block_stop"}))
    assert [e.text for e in stop if e.kind == "status"] == [THINKING_DONE_MARKER]


def test_a_text_block_stop_does_not_report_thinking_done(tmp_path):
    """content_block_stop은 사고 블록과 텍스트 블록에 모두 온다. 구분하지 않으면
    사고가 없던 턴에도 종료 마커가 나가 프론트가 켜지지 않은 상태를 끈다."""
    d = _driver(tmp_path)
    d._translate(StreamEvent({"type": "content_block_start",
                              "content_block": {"type": "text"}}))
    stop = d._translate(StreamEvent({"type": "content_block_stop"}))
    assert [e.text for e in stop if e.kind == "status"] == []


def test_thinking_deltas_do_not_leak_into_the_answer(tmp_path):
    """사고 델타를 본문 버퍼에 넣으면 모델의 추론이 답변에 섞인다. 이 경로에서는
    내용이 비어 있지만(실측 0자), 값이 생기는 날 조용히 섞이지 않게 잠근다."""
    d = _driver(tmp_path)
    events = d._translate(StreamEvent({
        "type": "content_block_delta",
        "delta": {"type": "thinking_delta", "thinking": "속으로 생각한 내용"}}))
    assert _texts(events) == []
    tail = d._translate(StreamEvent({"type": "content_block_stop"}))
    assert _texts(tail) == []


def test_signature_delta_is_ignored(tmp_path):
    """실측에서 사고 블록과 함께 오는 프레임이다. 본문도 트레이스도 아니다."""
    d = _driver(tmp_path)
    events = d._translate(StreamEvent({
        "type": "content_block_delta",
        "delta": {"type": "signature_delta", "signature": "abc"}}))
    assert events == []


@pytest.mark.asyncio
async def test_a_new_turn_does_not_inherit_the_previous_turns_delta_state(tmp_path):
    """델타 상태는 **인스턴스 수명**을 산다(드라이버가 프로젝트 하나를 계속 맡는다).

    턴이 중간에 버려지면(SSE 끊김, 프록시 타임아웃, 페이지 이동 — runner.py가
    실제로 그 경로를 갖고 있다) `_streamed_text`가 True로 남고 버퍼에 꼬리가
    갇힌다. 그 상태로 다음 턴이 시작하면 그 턴의 첫 AssistantMessage가 중복으로
    판정되어 **답변이 통째로 사라진다.** 턴 경계에서 씻어야 한다.
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
    """client_factory를 주입하는 위 테스트들은 이 경로를 타지 않으므로, 배선이
    빠져도 전부 통과한다(test_cli_settings의 같은 주석)."""
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
