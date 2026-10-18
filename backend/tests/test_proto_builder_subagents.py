# backend/tests/test_proto_builder_subagents.py — what the screen receives when the build
# agent hands work off to subagents.
#
# **Why this file exists.** When the orchestrating agent stopped running tools itself and
# launched subagents in parallel with the Agent tool, the build screen looked frozen for
# minutes. The cause was **not** that subagent progress was not arriving — the SDK was already
# sending per-agent heartbeats as `Task*` messages, but those four kinds are subclasses of
# `SystemMessage`, so they all slipped straight through `_translate`, which uses an equality
# check on `type(msg).__name__`, and were discarded.
#
# Measured evidence (claude_agent_sdk 0.2.143, Bedrock, 2 subagents in parallel):
#   TaskStarted   task_id, tool_use_id, description="Create a.txt with alpha"
#   TaskProgress  same task_id, last_tool_name="Write" → "Read"
#   TaskNotif.    status="completed", summary="Done. - Created …"
#   TaskUpdated   patch={"status":"completed", …}
#   the subagent's AssistantMessage.parent_tool_use_id == TaskStarted.tool_use_id
#     (the join succeeded for all 5 messages — that is the basis for attaching file paths to rows)
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


# ---- Rows open · update · close ----

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
    """A heartbeat with no tool name has nothing to put on screen. Sending an empty update
    just makes the frontend re-render every time while the row stays the same."""
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
    """This is the spot the SDK explicitly warns about: a background task, or a task killed
    by TaskStop, may report its end only here, without a TaskNotification. Watching only one
    side leaves that row spinning forever — exactly a recurrence of "it looks frozen"."""
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
    """Closing a row on `running`/`pending`/`paused` would make it vanish as soon as it starts."""
    client = FakeSdkClient(script=[
        TaskStartedMessage(task_id="a040", tool_use_id="toolu_1", description="x"),
        TaskUpdatedMessage(task_id="a040", status="running",
                           patch={"status": "running"}),
        ResultMessage(),
    ])
    events = await collect(_builder(tmp_path, client))
    assert [a["state"] for a in _activity(events)] == ["started"]


async def test_the_summary_survives_the_earlier_terminal(tmp_path):
    """**The measured order is TaskUpdated → TaskNotification, and the summary is only on the later one**
    (integration probe 2026-09-15). Closing the row on the first end and suppressing everything
    after it would always discard the summary the agent left — and that is the most useful line
    in the trace.

    So a summary that arrives late goes out once more. This is not closing the row twice:
    the frontend updates the trace line by `task_id` (upsert) — the responsibility for not
    counting the end twice lies with the **view** because the backend cannot know which end
    message is the last (the SDK states that sometimes only one of the two arrives)."""
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
    """An end for which the summary was already sent, or that has no new summary, is not emitted
    again — repeating the same fact makes the frontend's upsert spin uselessly every time."""
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


# ---- Subagent tool calls are not mistaken for orchestrator activity ----

async def test_a_subagent_tool_call_updates_its_row_not_the_main_status(tmp_path):
    """This is the core error of the previous screen: the tool calls of 3 subagents all came up
    as orchestrator `status`, and the pinned line flickered between names without knowing
    whose work it was."""
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
    """In the orchestrator trace, `tool_detail` deliberately leaves out the Write path —
    because the `file_changed` event already carries that path (tool_trace header).
    That logic does not hold for rows: `file_changed` does not say which agent it belongs
    to, so for a row to show its target, it has to come from here."""
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
    """A parent_tool_use_id whose TaskStarted was never seen — there is no way to know which row to attach it to.
    Sending it through as orchestrator status would make the subagent's work look like the orchestrator's."""
    client = FakeSdkClient(script=[
        AssistantMessage(parent_tool_use_id="toolu_ghost", content=[
            ToolUseBlock(id="r1", name="Read", input={"file_path": "/x/y.txt"})]),
        ResultMessage(),
    ])
    events = await collect(_builder(tmp_path, client))
    assert _activity(events) == []
    assert [e.kind for e in events if e.kind == "status"] == []


async def test_subagent_text_never_lands_in_the_bubble(tmp_path):
    """`forward_subagent_text` stays off — if three narratives mix into the bubble, the reason
    LiveActivityBar was built ("정신이 없다", it is chaotic) comes right back. The SDK does not
    even send it now, but the day that option is turned on, this discipline must be in the code."""
    client = FakeSdkClient(script=[
        TaskStartedMessage(task_id="a040", tool_use_id="toolu_1", description="x"),
        AssistantMessage(parent_tool_use_id="toolu_1",
                         content=[TextBlock(text="I'll write the page now.")]),
        ResultMessage(),
    ])
    events = await collect(_builder(tmp_path, client))
    assert [e.kind for e in events if e.kind == "message"] == []


# ---- The orchestrating agent's side (does not mix with subagents) ----

async def test_the_main_agent_still_gets_plain_status_events(tmp_path):
    client = FakeSdkClient(script=[
        AssistantMessage(content=[
            ToolUseBlock(id="b1", name="Bash", input={"command": "npm run build"})]),
        ResultMessage(),
    ])
    events = await collect(_builder(tmp_path, client))
    status = [e for e in events if e.kind == "status"]
    assert [e.text for e in status] == ["Bash"]
    # Parity with Discovery: send what it did as well (the previous build screen showed only
    # a bare `Bash` — only this side was not using what the tool_trace module had already solved).
    assert json.loads(status[0].payload)["detail"] == "npm run build"


async def test_launching_an_agent_is_traced_with_what_it_was_asked_to_do(tmp_path):
    """The Agent tool call itself is also an orchestrator tool call. If only a bare `Agent` remains,
    the trace has only the fact that "an agent was launched" and not what was delegated."""
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
    """The previous dedup folding looked only at the tool **name** — three consecutive Reads
    were mashed into a single `Read` line even if they were different files, and that was one of
    the reasons the screen looked frozen. claude_driver already folds by (name, detail)."""
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
    """Separately from attaching paths to rows, `file_changed` must still go out —
    the frontend's `changedPaths` (the list of build artifacts) looks only at that. Files written
    by subagents are real artifacts too."""
    b = _builder(tmp_path, FakeSdkClient())
    await b._on_post_tool_use(
        {"tool_name": "Write",
         "tool_input": {"file_path": f"{tmp_path}/prototype/app.js"}},
        "toolu_1", None)
    assert b._queue == [AgentEvent(kind="file_changed", path="prototype/app.js")]


# ---- Turn boundaries ----

async def test_rows_do_not_leak_into_the_next_turn(tmp_path):
    """A subagent's lifetime is the lifetime of the turn that launched it. If the join table
    survives past the turn, a tool_use_id reused in the next turn is attributed to the row of an
    already-dead agent — a finished agent comes back to life on screen.

    Rows left open when the turn ends are not closed separately by the backend: the frontend
    seals the bubble on `done` and folds the rows into the trace (usePrototypeStream), so
    synthesizing and sending an end event would fabricate a fact that does not exist."""
    class TwoTurns(FakeSdkClient):
        def __init__(self):
            super().__init__()
            self.turn = 0
            self.scripts = [
                # Turn 1: open a row and end without closing it.
                [TaskStartedMessage(task_id="a040", tool_use_id="toolu_1",
                                    description="x"),
                 ResultMessage()],
                # Turn 2: the same tool_use_id appears again.
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


# ---- Background is blocked by a CLI switch, not a hook ----

async def test_the_gate_leaves_agent_calls_alone(tmp_path):
    """A CLI switch keeps subagents from outliving the turn
    (cli_settings.cli_context_env). The switch runs even `run_in_background: true` in the
    foreground, so if a hook rejected it, that would only add one useless round trip for the model."""
    b = _builder(tmp_path, None)
    out = await b._on_pre_tool_use(
        {"tool_name": "Agent",
         "tool_input": {"description": "화면 골격", "run_in_background": True}},
        "t1", None)
    assert out == {}


def test_the_build_cli_runs_with_background_tasks_off(tmp_path, monkeypatch):
    """Other tests that inject client_factory do not take this path, so they all pass even if
    the wiring is missing — this one looks at the env the real factory builds."""
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

    assert captured["options"].env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
