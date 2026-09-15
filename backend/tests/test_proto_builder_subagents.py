# backend/tests/test_proto_builder_subagents.py — 빌드 에이전트가 일을 서브에이전트에
# 넘겼을 때 화면이 무엇을 받는가.
#
# **왜 이 파일이 있는가.** 총괄 에이전트가 직접 도구를 돌리지 않고 Agent 도구로
# 서브에이전트를 병렬로 띄우면서, 빌드 화면이 몇 분간 멈춘 것처럼 보였다. 원인은
# 서브에이전트 진행 상황이 오지 않는 것이 **아니었다** — SDK는 `Task*` 메시지로
# 에이전트별 하트비트를 이미 보내고 있었고, 그 네 종류가 `SystemMessage`의
# 서브클래스라서 `type(msg).__name__` 동등 비교를 쓰는 `_translate`를 전부
# 그냥 통과해 버려졌다.
#
# 실측 근거(claude_agent_sdk 0.2.143, Bedrock, 서브에이전트 2개 병렬):
#   TaskStarted   task_id, tool_use_id, description="Create a.txt with alpha"
#   TaskProgress  같은 task_id, last_tool_name="Write" → "Read"
#   TaskNotif.    status="completed", summary="Done. - Created …"
#   TaskUpdated   patch={"status":"completed", …}
#   서브에이전트의 AssistantMessage.parent_tool_use_id == TaskStarted.tool_use_id
#     (5개 메시지 전부 조인 성공 — 그것이 행에 파일 경로를 붙일 수 있는 근거다)
from __future__ import annotations

import json

from aipds.models import AgentEvent
from aipds.proto.builder import PrototypeBuilder
from fakes.fake_sdk import (AssistantMessage, FakeSdkClient, ResultMessage,
                            TaskNotificationMessage, TaskProgressMessage,
                            TaskStartedMessage, TaskUpdatedMessage, TextBlock,
                            ToolUseBlock)


def _builder(tmp_path, client, **kw):
    return PrototypeBuilder(
        workspace=str(tmp_path),
        config_dir=str(tmp_path / "config"),
        session_id="11111111-2222-3333-4444-555555555555",
        resume=False,
        client_factory=lambda: client,
        **kw,
    )


async def collect(builder, text="go"):
    return [ev async for ev in builder.run(text)]


def _activity(events: list[AgentEvent]) -> list[dict]:
    return [json.loads(e.payload or "{}")
            for e in events if e.kind == "agent_activity"]


# ---- 행이 열리고 · 갱신되고 · 닫힌다 ----

async def test_a_started_task_opens_a_row_with_its_assignment_as_the_label(tmp_path):
    client = FakeSdkClient(script=[
        TaskStartedMessage(task_id="a040", tool_use_id="toolu_1",
                           description="화면 골격 만들기"),
        ResultMessage(),
    ])
    events = await collect(_builder(tmp_path, client))
    assert _activity(events) == [
        {"task_id": "a040", "state": "started", "label": "화면 골격 만들기"}]


async def test_progress_reports_which_tool_that_agent_is_running(tmp_path):
    client = FakeSdkClient(script=[
        TaskStartedMessage(task_id="a040", tool_use_id="toolu_1",
                           description="화면 골격"),
        TaskProgressMessage(task_id="a040", last_tool_name="Write",
                            description="Writing app/page.tsx"),
        ResultMessage(),
    ])
    events = await collect(_builder(tmp_path, client))
    assert _activity(events)[1] == {"task_id": "a040", "state": "progress",
                                    "tool": "Write"}


async def test_progress_with_nothing_to_show_is_not_emitted(tmp_path):
    """도구 이름이 없는 하트비트는 화면에 실을 것이 없다. 빈 갱신을 보내면
    프론트가 매번 리렌더만 하고 행은 그대로다."""
    client = FakeSdkClient(script=[
        TaskStartedMessage(task_id="a040", tool_use_id="toolu_1", description="x"),
        TaskProgressMessage(task_id="a040", last_tool_name=None),
        ResultMessage(),
    ])
    events = await collect(_builder(tmp_path, client))
    assert [a["state"] for a in _activity(events)] == ["started"]


async def test_a_completed_notification_closes_the_row_with_its_summary(tmp_path):
    client = FakeSdkClient(script=[
        TaskStartedMessage(task_id="a040", tool_use_id="toolu_1", description="x"),
        TaskNotificationMessage(task_id="a040", status="completed",
                                summary="Done. app/page.tsx를 만들었다"),
        ResultMessage(),
    ])
    events = await collect(_builder(tmp_path, client))
    assert _activity(events)[1] == {
        "task_id": "a040", "state": "done", "status": "completed",
        "summary": "Done. app/page.tsx를 만들었다"}


async def test_a_task_updated_terminal_status_also_closes_the_row(tmp_path):
    """SDK가 명시적으로 경고하는 자리다: 백그라운드 태스크나 TaskStop으로 죽은
    태스크는 TaskNotification 없이 여기서만 종료를 보고할 수 있다. 한쪽만 보면
    그 행이 영원히 도는 채로 남는다 — 정확히 "멈춘 것 같다"의 재발이다."""
    client = FakeSdkClient(script=[
        TaskStartedMessage(task_id="a040", tool_use_id="toolu_1", description="x"),
        TaskUpdatedMessage(task_id="a040", status="killed",
                           patch={"status": "killed", "end_time": 1}),
        ResultMessage(),
    ])
    events = await collect(_builder(tmp_path, client))
    assert _activity(events)[1] == {"task_id": "a040", "state": "done",
                                    "status": "killed"}


async def test_a_non_terminal_update_is_not_a_close(tmp_path):
    """`running`/`pending`/`paused`로 행을 닫으면 시작하자마자 사라진다."""
    client = FakeSdkClient(script=[
        TaskStartedMessage(task_id="a040", tool_use_id="toolu_1", description="x"),
        TaskUpdatedMessage(task_id="a040", status="running",
                           patch={"status": "running"}),
        ResultMessage(),
    ])
    events = await collect(_builder(tmp_path, client))
    assert [a["state"] for a in _activity(events)] == ["started"]


async def test_the_summary_survives_the_earlier_terminal(tmp_path):
    """**실측 순서가 TaskUpdated → TaskNotification이고, 요약은 뒤쪽에만 있다**
    (integration 프로브 2026-09-15). 첫 종료로 행을 닫고 그 뒤를 전부 억제하면
    에이전트가 남긴 요약이 언제나 버려진다 — 트레이스에서 가장 쓸모 있는 줄이
    그것이다.

    그래서 늦게 온 요약은 한 번 더 나간다. 행을 두 번 닫는 것이 아니다:
    프론트가 `task_id`로 트레이스 줄을 갱신한다(upsert) — 종료를 두 번 세지 않는
    책임이 **뷰**에 있는 이유는, 어느 종료 메시지가 마지막인지 백엔드가 알 수
    없기 때문이다(SDK는 둘 중 하나만 오는 경우도 있다고 명시한다)."""
    client = FakeSdkClient(script=[
        TaskStartedMessage(task_id="a040", tool_use_id="toolu_1", description="x"),
        TaskUpdatedMessage(task_id="a040", status="completed",
                           patch={"status": "completed"}),
        TaskNotificationMessage(task_id="a040", status="completed", summary="끝냈다"),
        ResultMessage(),
    ])
    events = await collect(_builder(tmp_path, client))
    activity = _activity(events)
    assert [a["state"] for a in activity] == ["started", "done", "done"]
    assert "summary" not in activity[1]
    assert activity[2]["summary"] == "끝냈다"


async def test_a_terminal_that_adds_nothing_is_suppressed(tmp_path):
    """요약을 이미 보냈거나 새 요약이 없는 종료는 다시 내보내지 않는다 — 같은
    사실을 반복하면 프론트의 upsert가 매번 헛돈다."""
    client = FakeSdkClient(script=[
        TaskStartedMessage(task_id="a040", tool_use_id="toolu_1", description="x"),
        TaskNotificationMessage(task_id="a040", status="completed", summary="끝냈다"),
        TaskUpdatedMessage(task_id="a040", status="completed",
                           patch={"status": "completed"}),
        TaskNotificationMessage(task_id="a040", status="completed", summary="끝냈다"),
        ResultMessage(),
    ])
    events = await collect(_builder(tmp_path, client))
    activity = _activity(events)
    assert [a["state"] for a in activity] == ["started", "done"]
    assert activity[1]["summary"] == "끝냈다"


# ---- 서브에이전트의 도구 호출이 총괄 활동으로 오인되지 않는다 ----

async def test_a_subagent_tool_call_updates_its_row_not_the_main_status(tmp_path):
    """이것이 종전 화면의 핵심 오류다: 서브에이전트 3개의 도구 호출이 전부 총괄
    `status`로 올라와, 고정 줄이 누구의 일인지 모르는 채 이름들 사이에서
    깜빡였다."""
    client = FakeSdkClient(script=[
        TaskStartedMessage(task_id="a040", tool_use_id="toolu_1", description="x"),
        AssistantMessage(parent_tool_use_id="toolu_1", content=[
            ToolUseBlock(id="w1", name="Write",
                         input={"file_path": f"{tmp_path}/prototype/app.tsx"})]),
        ResultMessage(),
    ])
    events = await collect(_builder(tmp_path, client))
    assert [e.kind for e in events if e.kind == "status"] == []
    assert _activity(events)[1] == {"task_id": "a040", "state": "progress",
                                    "tool": "Write", "detail": "prototype/app.tsx"}


async def test_a_subagent_row_shows_the_file_it_is_writing(tmp_path):
    """총괄 트레이스에서는 Write의 경로를 `tool_detail`이 일부러 빼 왔다 —
    `file_changed` 이벤트가 이미 그 경로를 들고 오기 때문이다(tool_trace 헤더).
    행에는 그 논리가 성립하지 않는다: `file_changed`는 어느 에이전트의 것인지
    말해 주지 않으므로, 행이 대상을 보여주려면 여기서 와야 한다."""
    client = FakeSdkClient(script=[
        TaskStartedMessage(task_id="a040", tool_use_id="toolu_1", description="x"),
        AssistantMessage(parent_tool_use_id="toolu_1", content=[
            ToolUseBlock(id="e1", name="Edit",
                         input={"file_path": f"{tmp_path}/prototype/x.css"})]),
        ResultMessage(),
    ])
    events = await collect(_builder(tmp_path, client))
    assert _activity(events)[1]["detail"] == "prototype/x.css"


async def test_a_subagent_tool_call_for_an_unknown_task_is_dropped(tmp_path):
    """TaskStarted를 못 본 parent_tool_use_id — 어느 행에 붙일지 알 수 없다.
    총괄 status로 흘려보내면 서브에이전트의 일이 총괄의 일로 보인다."""
    client = FakeSdkClient(script=[
        AssistantMessage(parent_tool_use_id="toolu_ghost", content=[
            ToolUseBlock(id="r1", name="Read", input={"file_path": "/x/y.txt"})]),
        ResultMessage(),
    ])
    events = await collect(_builder(tmp_path, client))
    assert _activity(events) == []
    assert [e.kind for e in events if e.kind == "status"] == []


async def test_subagent_text_never_lands_in_the_bubble(tmp_path):
    """`forward_subagent_text`는 끄고 간다 — 서사 세 개가 말풍선에 섞이면
    LiveActivityBar를 만든 이유("정신이 없다")가 그대로 재발한다. 지금은 SDK가
    보내지도 않지만, 그 옵션을 켜는 날 이 규율이 코드에 있어야 한다."""
    client = FakeSdkClient(script=[
        TaskStartedMessage(task_id="a040", tool_use_id="toolu_1", description="x"),
        AssistantMessage(parent_tool_use_id="toolu_1",
                         content=[TextBlock(text="I'll write the page now.")]),
        ResultMessage(),
    ])
    events = await collect(_builder(tmp_path, client))
    assert [e.kind for e in events if e.kind == "message"] == []


# ---- 총괄 에이전트 쪽 (서브에이전트와 섞이지 않는다) ----

async def test_the_main_agent_still_gets_plain_status_events(tmp_path):
    client = FakeSdkClient(script=[
        AssistantMessage(content=[
            ToolUseBlock(id="b1", name="Bash", input={"command": "npm run build"})]),
        ResultMessage(),
    ])
    events = await collect(_builder(tmp_path, client))
    status = [e for e in events if e.kind == "status"]
    assert [e.text for e in status] == ["Bash"]
    # Discovery와 동등: 무엇을 했는지까지 보낸다(종전 빌드 화면은 맨 `Bash`만
    # 보였다 — tool_trace 모듈이 이미 해결한 문제를 이쪽만 안 쓰고 있었다).
    assert json.loads(status[0].payload)["detail"] == "npm run build"


async def test_launching_an_agent_is_traced_with_what_it_was_asked_to_do(tmp_path):
    """Agent 도구 호출 자체도 총괄의 도구 호출이다. 맨 `Agent`만 남으면
    트레이스에서 "에이전트를 띄웠다"는 사실만 있고 무엇을 맡겼는지가 없다."""
    client = FakeSdkClient(script=[
        AssistantMessage(content=[
            ToolUseBlock(id="t1", name="Agent",
                         input={"description": "화면 골격 만들기",
                                "subagent_type": "general-purpose",
                                "prompt": "아주 긴 프롬프트" * 50})]),
        ResultMessage(),
    ])
    events = await collect(_builder(tmp_path, client))
    status = [e for e in events if e.kind == "status"]
    assert status[0].text == "Agent"
    assert json.loads(status[0].payload)["detail"] == "화면 골격 만들기"


async def test_repeated_reads_of_different_files_are_not_collapsed(tmp_path):
    """종전 중복 접기는 도구 **이름**만 봤다 — 연속된 Read 세 번이 서로 다른
    파일이어도 `Read` 한 줄로 뭉개졌고, 그것이 화면이 멈춘 듯 보인 이유 중
    하나다. claude_driver는 이미 (name, detail)로 접는다."""
    client = FakeSdkClient(script=[
        AssistantMessage(content=[
            ToolUseBlock(id="1", name="Read",
                         input={"file_path": "aiplc-docs/a.md"}),
            ToolUseBlock(id="2", name="Read",
                         input={"file_path": "aiplc-docs/b.md"}),
            ToolUseBlock(id="3", name="Read",
                         input={"file_path": "aiplc-docs/b.md"})]),
        ResultMessage(),
    ])
    events = await collect(_builder(tmp_path, client))
    details = [json.loads(e.payload)["detail"]
               for e in events if e.kind == "status"]
    assert details == ["aiplc-docs/a.md", "aiplc-docs/b.md"]


async def test_file_changed_still_reaches_the_artifact_list(tmp_path):
    """행에 경로를 붙이는 것과 별개로 `file_changed`는 그대로 나가야 한다 —
    프론트의 `changedPaths`(빌드 산출물 목록)가 그것만 본다. 서브에이전트가 쓴
    파일도 실제 산출물이다."""
    b = _builder(tmp_path, FakeSdkClient())
    await b._on_post_tool_use(
        {"tool_name": "Write",
         "tool_input": {"file_path": f"{tmp_path}/prototype/app.js"}},
        "toolu_1", None)
    assert b._queue == [AgentEvent(kind="file_changed", path="prototype/app.js")]


# ---- 턴 경계 ----

async def test_rows_do_not_leak_into_the_next_turn(tmp_path):
    """서브에이전트의 수명은 그것을 띄운 턴의 수명이다. 조인 표가 턴을 넘어
    살아 있으면, 다음 턴에 재활용된 tool_use_id가 이미 죽은 에이전트의 행으로
    귀속된다 — 화면에 끝난 에이전트가 되살아난다.

    턴이 끝날 때 열린 채 남은 행은 백엔드가 따로 닫지 않는다: 프론트가 `done`에서
    말풍선을 봉하고 행들을 트레이스로 접으므로(usePrototypeStream), 종료 이벤트를
    합성해 보내면 없는 사실을 만드는 것이 된다."""
    class TwoTurns(FakeSdkClient):
        def __init__(self):
            super().__init__()
            self.turn = 0
            self.scripts = [
                # 턴 1: 행을 열고 닫지 않은 채 끝난다.
                [TaskStartedMessage(task_id="a040", tool_use_id="toolu_1",
                                    description="x"),
                 ResultMessage()],
                # 턴 2: 같은 tool_use_id가 다시 나타난다.
                [AssistantMessage(parent_tool_use_id="toolu_1", content=[
                    ToolUseBlock(id="w1", name="Write",
                                 input={"file_path": "prototype/app.tsx"})]),
                 ResultMessage()],
            ]

        async def receive_response(self):
            script = self.scripts[self.turn]
            self.turn += 1
            for msg in script:
                yield msg

    b = _builder(tmp_path, TwoTurns())
    await collect(b, "one")
    second = await collect(b, "two")
    assert _activity(second) == []


# ---- 백그라운드 서브에이전트는 게이트가 막는다 ----

async def test_the_gate_denies_a_background_agent(tmp_path):
    """실측(2026-09-15): 모델이 `run_in_background: true`를 고르면 총괄이 턴을
    끝내고 서브에이전트는 계속 돈다. `done`에서 SSE가 닫히므로 그 뒤의 행 갱신은
    화면에 닿지 못하고, 사용자의 다음 메시지는 워크스페이스를 아직 고치고 있는
    에이전트들 위로 간다. 턴의 수명이 그 턴이 시킨 일의 수명을 덮어야 한다."""
    b = _builder(tmp_path, None)
    out = await b._on_pre_tool_use(
        {"tool_name": "Agent",
         "tool_input": {"description": "화면 골격", "run_in_background": True}},
        "t1", None)
    decision = out["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "run_in_background" in decision["permissionDecisionReason"]


async def test_the_gate_passes_a_foreground_agent_with_an_empty_dict(tmp_path):
    """통과는 빈 dict다 — "allow"는 can_use_tool까지 건너뛰어 질문 왕복을 죽인다."""
    b = _builder(tmp_path, None)
    out = await b._on_pre_tool_use(
        {"tool_name": "Agent", "tool_input": {"description": "화면 골격"}}, "t1", None)
    assert out == {}


def test_the_agent_tool_is_wired_into_the_pretooluse_matcher(tmp_path, monkeypatch):
    """판정부가 있어도 matcher가 `Agent`를 걸지 않으면 훅이 불리지 않는다 —
    게이트가 이름만 남는다."""
    from aipds.proto.builder import _default_client_factory

    captured = {}

    class FakeClient:
        def __init__(self, options=None):
            captured["options"] = options

    import claude_agent_sdk
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", FakeClient)

    b = PrototypeBuilder(
        workspace=str(tmp_path), config_dir=str(tmp_path / "config"),
        session_id="11111111-2222-3333-4444-555555555555", resume=False)
    _default_client_factory(b)()

    matchers = captured["options"].hooks["PreToolUse"]
    assert any("Agent" in m.matcher for m in matchers)
    # AskUserQuestion은 여전히 걸리지 않아야 한다(질문 왕복이 그 콜백에 있다).
    for m in matchers:
        assert "AskUserQuestion" not in m.matcher
