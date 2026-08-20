# backend/tests/test_proto_builder.py — ported from harness/tests/test_sdk_driver.py.
# The driver logic is unchanged; only its home and constructor moved.
from __future__ import annotations

from aipds.models import AgentEvent
from aipds.proto.builder import PrototypeBuilder
from fakes.fake_sdk import (AssistantMessage, FakeSdkClient, ResultMessage,
                            TextBlock, ToolUseBlock)


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


async def test_text_and_result_translate(tmp_path):
    client = FakeSdkClient(script=[
        AssistantMessage(content=[TextBlock(text="working on it")]),
        ResultMessage(subtype="success"),
    ])
    b = _builder(tmp_path, client)
    events = await collect(b)
    kinds = [(e.kind, e.text) for e in events]
    assert ("message", "working on it") in kinds
    assert events[-1].kind == "done"
    assert client.queries == ["go"]


async def test_tool_use_status_deduped(tmp_path):
    client = FakeSdkClient(script=[
        AssistantMessage(content=[ToolUseBlock(id="1", name="Bash", input={}),
                                  ToolUseBlock(id="2", name="Bash", input={})]),
        AssistantMessage(content=[ToolUseBlock(id="3", name="Write",
                                               input={"file_path": "x"})]),
        ResultMessage(),
    ])
    b = _builder(tmp_path, client)
    events = await collect(b)
    statuses = [e.text for e in events if e.kind == "status"]
    assert statuses == ["Bash", "Write"]


async def test_client_error_yields_sanitized_error(tmp_path):
    class Boom(FakeSdkClient):
        async def receive_response(self):
            raise RuntimeError("AWS_SECRET=xyz leaked")
            yield  # pragma: no cover

    b = _builder(tmp_path, Boom())
    events = await collect(b)
    assert events[-1].kind == "error"
    assert "xyz" not in (events[-1].text or "")


async def test_second_turn_reuses_connected_client(tmp_path):
    client = FakeSdkClient(script=[ResultMessage()])
    b = _builder(tmp_path, client)
    await collect(b, "one")
    await collect(b, "two")
    assert client.queries == ["one", "two"]


async def test_turn_already_in_progress(tmp_path):
    client = FakeSdkClient(script=[ResultMessage()])
    b = _builder(tmp_path, client)
    b._turn_active = True
    events = await collect(b)
    assert events[0].kind == "error"
    assert "in progress" in events[0].text


async def test_post_tool_hook_emits_file_changed(tmp_path):
    b = _builder(tmp_path, FakeSdkClient())
    await b._on_post_tool_use(
        {"tool_name": "Write",
         "tool_input": {"file_path": f"{tmp_path}/prototype/app.js"}},
        "toolu_1", None)
    # Asserts on the queue itself rather than the deleted drain_queue(): the
    # hook's behavior under test is unchanged, only the batch-pop accessor is
    # gone (it WAS the message-loss defect -- see _relay_queue).
    assert b._queue == [
        AgentEvent(kind="file_changed", path="prototype/app.js")]


async def test_post_tool_hook_rejects_escape(tmp_path):
    b = _builder(tmp_path, FakeSdkClient())
    await b._on_post_tool_use(
        {"tool_name": "Write", "tool_input": {"file_path": "/etc/passwd"}},
        "toolu_1", None)
    events = b._queue
    assert [e.kind for e in events] == ["status"]
    assert "outside workspace" in events[0].text


async def test_disconnect_closes_client_and_is_idempotent(tmp_path):
    """NEW vs the VM era: stopping the VM used to reclaim everything. Now the
    idle timer / close path must explicitly disconnect, or the claude
    subprocess keeps holding ~300-500MB."""
    client = FakeSdkClient(script=[ResultMessage()])
    b = _builder(tmp_path, client)
    await collect(b)
    await b.disconnect()
    await b.disconnect()
    assert client.disconnect_calls == 1


async def test_disconnect_without_a_turn_is_a_noop(tmp_path):
    client = FakeSdkClient()
    b = _builder(tmp_path, client)
    await b.disconnect()
    assert client.disconnect_calls == 0


# ---- the real SDK options (these tests use the DEFAULT client factory, not a
# fake, because the wiring itself is what must be pinned) ----

_SID = "11111111-2222-3333-4444-555555555555"


def _real_options(resume=False, **kw):
    """Capture the ClaudeAgentOptions the default factory hands the SDK."""
    import claude_agent_sdk
    import aipds.proto.builder as bmod

    captured = {}

    class Spy:
        def __init__(self, options=None):
            captured["options"] = options

    original, claude_agent_sdk.ClaudeSDKClient = claude_agent_sdk.ClaudeSDKClient, Spy
    try:
        builder = bmod.PrototypeBuilder(
            workspace="/tmp/ws", config_dir="/opt/aipds/proto-config",
            session_id=_SID, resume=resume, **kw)
        builder._factory()
    finally:
        claude_agent_sdk.ClaudeSDKClient = original
    return captured["options"]


def test_only_our_own_skills_are_enabled():
    """Never `"all"`: that also enables the CLI's BUNDLED skills, and one of
    them (`run` -- "Launch and drive this project's app... browser-driven") got
    a build agent to start Playwright chromium, whose port-3000 target SIGKILLed
    the AI-PDS frontend mid-workshop (2026-08-01 16:13/16:18; the coredump's
    Unit was aipds-backend.service, so the browser was ours).

    An explicit name list makes the SDK emit `Skill(shadcn-design)` instead of a
    bare `Skill`, so bundled skills never enter the turn. Adding a skill to
    proto-config/skills/ now also means adding its name here -- that cost is the
    point of this test."""
    assert _real_options().skills == ["shadcn-design"]


def test_setting_sources_stay_open_so_skills_can_be_discovered():
    """`skills` cannot find anything if the filesystem sources are closed;
    "user" here means our config dir, not the operator's home."""
    assert _real_options().setting_sources == ["user", "project"]


def test_config_dir_is_always_injected():
    """The guard against the bundled binary falling back to the backend user's
    personal ~/.claude (their own skills/agents/CLAUDE.md leaking into every
    build). There is exactly one options site, so this pins it."""
    assert _real_options().env["CLAUDE_CONFIG_DIR"] == "/opt/aipds/proto-config"


def test_we_do_not_restrict_the_agents_own_tools():
    """The build agent needs Bash/Write/Edit, which stay unrestricted because
    they never appear here -- the SDK appends "Skill" to whatever we pass for
    skills="all", so narrowing this list would narrow that too.

    Updated for build_complete's wiring (Task 3): allowed_tools now carries
    exactly our one custom MCP tool, not the empty list an earlier draft of
    this test pinned. That is additive, not a restriction -- Bash/Write/Edit
    are still absent from this list and still unrestricted."""
    from aipds.proto.tools import BUILD_COMPLETE_TOOL
    assert _real_options().allowed_tools == [BUILD_COMPLETE_TOOL]


def test_file_checkpointing_stays_off_so_session_store_is_legal():
    """The SDK raises ValueError when session_store is combined with
    enable_file_checkpointing -- that would break durable transcripts."""
    assert _real_options().enable_file_checkpointing is False


# ---- resume vs. session_id (the flag pair the CLI validates) ----

def test_fresh_session_pins_the_id_we_chose():
    """A first build must land on OUR session id, because that id is what
    session.py persisted and what the next resume will look up."""
    options = _real_options(resume=False)
    assert options.session_id == _SID
    assert options.resume is None


def test_resume_does_not_also_pass_session_id():
    """`--session-id` together with `--resume` is rejected outright by the CLI
    ("can only be used with --continue or --resume if --fork-session is also
    specified"), so every resumed build died at connect(). `--resume` alone
    already keeps the same session id, so passing both buys nothing."""
    options = _real_options(resume=True)
    assert options.resume == _SID
    assert options.session_id is None


def test_bypass_permissions_is_the_default():
    """Workshop builds are unattended: nothing is watching to approve a Write,
    so anything short of bypassPermissions stalls the turn forever."""
    assert _real_options().permission_mode == "bypassPermissions"


def test_permission_mode_is_overridable_for_a_stricter_run():
    assert _real_options(
        permission_mode="acceptEdits").permission_mode == "acceptEdits"


def test_an_unknown_permission_mode_is_rejected_at_construction():
    """A typo ("bypassPermission") would otherwise reach the CLI as an unknown
    --permission-mode and fail at connect() -- the same class of late,
    opaque failure as the --session-id/--resume clash. Fail loudly instead."""
    import pytest
    from aipds.proto.builder import PrototypeBuilder

    with pytest.raises(ValueError, match="bypassPermission"):
        PrototypeBuilder(workspace="/tmp/ws", config_dir="/tmp/cfg",
                         session_id=_SID, resume=False,
                         permission_mode="bypassPermission")


def test_ask_user_question_still_reaches_our_callback_under_bypass():
    """The SDK warns that bypassPermissions shadows can_use_tool entirely. That
    is true for ordinary tools (Bash/Write never reach us) but NOT for
    AskUserQuestion, which is the one tool our callback exists to intercept --
    verified against the real CLI. So the warning is a false positive for this
    wiring, and the questions flow depends on us keeping the callback set."""
    assert _real_options().can_use_tool is not None


async def test_connecting_mutes_the_shadowed_callback_warning(tmp_path):
    """The false-positive warning fires on every connect(); left alone it
    trains operators to ignore backend warnings. Muted for this one category
    only -- a different SDK warning must still get through.

    The warning is raised here explicitly rather than awaited from a fake
    client: only the REAL SDK's connect() emits it, so a fake-based assertion
    would pass whether or not the filter was installed (it did, until this
    test was rewritten). What is under test is that _ensure_client() installs
    a filter which swallows that category and nothing else.
    """
    import warnings

    from claude_agent_sdk import CanUseToolShadowedWarning
    from fakes.fake_sdk import FakeSdkClient

    b = _builder(tmp_path, FakeSdkClient())
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await b._ensure_client()  # installs the filter
        # Stand in for what the real SDK emits inside connect().
        warnings.warn("can_use_tool will not be invoked: ...",
                      CanUseToolShadowedWarning)
        warnings.warn("an unrelated SDK warning", UserWarning)
    messages = [str(w.message) for w in caught]
    assert not [m for m in messages if "can_use_tool will not be invoked" in m]
    assert "an unrelated SDK warning" in messages


def test_the_cli_accepts_the_flags_we_build_for_a_resume():
    """Pin the pair against the real CLI arg builder rather than only against
    our own field choices -- the constraint being satisfied lives in the CLI,
    not in this repo."""
    from claude_agent_sdk._internal.transport.subprocess_cli import (
        SubprocessCLITransport)

    async def _empty():  # pragma: no cover - never iterated
        return
        yield

    for resume in (False, True):
        transport = SubprocessCLITransport(
            prompt=_empty(), options=_real_options(resume=resume))
        # _build_command() refuses to run before connect() resolves the binary;
        # we only want the argv it would build, not a subprocess.
        transport._cli_path = "/usr/bin/claude"
        flags = transport._build_command()
        session_id_flags = [f for f in flags if f.startswith("--session-id")]
        resume_flags = [f for f in flags if f.startswith("--resume")]
        # Exactly one of the two, never both, and never --fork-session (which
        # would strand the transcript under a NEW id the store never sees).
        assert len(session_id_flags) + len(resume_flags) == 1, flags
        assert "--fork-session" not in flags


# ---- build_complete MCP 도구 배선 ----

def test_mcp_server_and_allowed_tools_are_wired(tmp_path, monkeypatch):
    """_default_client_factory가 MCP 서버와 allowed_tools를 실제로 넘기는지.

    client_factory를 주입하는 다른 테스트들은 이 경로를 전혀 타지 않으므로,
    배선이 빠져도 그 테스트들은 전부 통과한다 — 그래서 옵션을 직접 붙잡는다.
    """
    from aipds.proto.builder import _default_client_factory
    from aipds.proto.tools import BUILD_COMPLETE_TOOL, PROTO_MCP_SERVER_NAME

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

    options = captured["options"]
    assert PROTO_MCP_SERVER_NAME in options.mcp_servers
    assert BUILD_COMPLETE_TOOL in options.allowed_tools
    # 스킬 목록이 살아 있어야 한다 — SDK가 allowed_tools를 복사한 뒤 스킬 항목을
    # 덧붙이므로(subprocess_cli.py:434-452) build_complete와 공존한다.
    # shadcn-design이 이 값에 달려 있고, "all"로 되돌리면 번들 `run` 스킬이
    # 함께 들어와 프론트엔드를 죽인 사고가 재현된다.
    assert options.skills == ["shadcn-design"]


async def test_the_tool_queues_a_build_complete_event(tmp_path):
    """도구의 emit이 빌더 큐로 가는지 — _on_post_tool_use와 같은 경로여야
    _relay_queue의 소유권 규율(배달 후 pop)을 받는다."""
    from aipds.proto.builder import _proto_tools_for

    (tmp_path / "prototype").mkdir()
    (tmp_path / "prototype" / "index.html").write_text("x")

    b = _builder(tmp_path, FakeSdkClient(script=[]))
    handler = {t.name: t.handler for t in _proto_tools_for(b)}["build_complete"]

    await handler({"summary": "만들었다"})

    assert [e.kind for e in b._queue] == ["build_complete"]


async def test_a_queued_completion_is_relayed_before_the_terminal_done(tmp_path):
    """build_complete가 done보다 먼저 나가는지 — 진짜 run()으로 확인한다.

    proto/session.py의 done 가드와 유예 타이머가 이 순서에 의존한다. 세션
    테스트는 FakeBuilder가 스크립트 순서대로 내보내므로 이 규율을 검증하지
    못한다 -- run()이 terminal 이벤트를 held하고 큐를 먼저 비우기 때문에
    성립하는 것이고(builder.py의 call site 4), 그 규율을 되돌리면 여기가
    먼저 실패해야 한다.

    sse.ts가 done에서 EventSource를 닫으므로, 순서가 뒤집히면 완료 이벤트가
    클라이언트에 닿지 않고 완료 카드가 영원히 뜨지 않는다.
    """
    from aipds.proto.builder import _proto_tools_for

    (tmp_path / "prototype").mkdir()
    (tmp_path / "prototype" / "index.html").write_text("x")

    client = FakeSdkClient(script=[ResultMessage()])
    b = _builder(tmp_path, client)
    # 턴이 시작되기 전에 도구가 호출된 것처럼 큐에 넣는다 — 실제로는
    # ResultMessage 직전에 호출된다.
    handler = {t.name: t.handler for t in _proto_tools_for(b)}["build_complete"]
    await handler({"summary": "만들었다"})

    events = await collect(b)

    kinds = [e.kind for e in events]
    assert "build_complete" in kinds
    assert kinds.index("build_complete") < kinds.index("done")
    assert kinds[-1] == "done"


# ---- Bash 게이트 (PreToolUse) ----
#
# 2026-08-01: 빌드 에이전트가 Playwright chromium을 띄웠고 그 검증이 포트 3000을
# 겨냥해 AI-PDS 프론트엔드가 SIGKILL로 죽었다. 그때의 완화책은 skills 좁히기와
# CLAUDE.md 산문뿐이었고, builder.py의 skills 주석이 스스로 적어 뒀듯 스킬 목록은
# **컨텍스트 필터이지 샌드박스가 아니다** — Bash는 그대로 열려 있었다.

def test_the_bash_gate_is_wired_as_a_pretooluse_hook(tmp_path, monkeypatch):
    """빌드는 bypassPermissions로 돌아 can_use_tool이 Bash에 도달하지 않는다
    (SDK의 _get_can_use_tool_shadowed_warning: "To gate every tool call, use a
    PreToolUse hook instead"). 실효 게이트는 PreToolUse뿐이다."""
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
    assert any("Bash" in m.matcher for m in matchers), (
        "PreToolUse가 Bash를 걸지 않는다 — 산문만 남고 강제가 없다")


def test_the_bash_gate_never_matches_askuserquestion(tmp_path, monkeypatch):
    """matcher에 AskUserQuestion을 넣으면 질문 왕복 전체가 죽는다.

    PreToolUse가 *allow*를 돌려주면 can_use_tool을 건너뛰는데(SDK types.py),
    질문을 SSE 이벤트로 바꾸는 가로채기가 그 콜백에 있다. claude_driver.py가
    같은 함정을 주석으로 남겨 뒀다.
    """
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

    for matcher in captured["options"].hooks["PreToolUse"]:
        assert "AskUserQuestion" not in matcher.matcher


async def test_the_gate_denies_browser_automation(tmp_path):
    b = _builder(tmp_path, None)
    out = await b._on_pre_tool_use(
        {"tool_name": "Bash", "tool_input": {"command": "npx playwright test"}},
        "t1", None)
    decision = out["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "playwright" in decision["permissionDecisionReason"]


async def test_the_gate_denies_a_server_that_would_hold_a_port(tmp_path):
    b = _builder(tmp_path, None)
    out = await b._on_pre_tool_use(
        {"tool_name": "Bash", "tool_input": {"command": "npm run start"}},
        "t1", None)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


async def test_the_gate_passes_the_build_with_an_empty_dict(tmp_path):
    """**통과는 빈 dict다.** "allow"를 돌려주면 can_use_tool까지 건너뛰어
    AskUserQuestion 가로채기가 죽는다 — claude_driver._on_pre_tool_use와 같은
    규율이다."""
    b = _builder(tmp_path, None)
    out = await b._on_pre_tool_use(
        {"tool_name": "Bash", "tool_input": {"command": "npm run build"}},
        "t1", None)
    assert out == {}


async def test_the_gate_ignores_tools_it_does_not_judge(tmp_path):
    """matcher와 이 분기가 어긋나도 조용히 통과해야 한다 — 알 수 없는 도구를
    막으면 빌드가 멈춘다."""
    b = _builder(tmp_path, None)
    out = await b._on_pre_tool_use(
        {"tool_name": "Write", "tool_input": {"file_path": "app/page.tsx"}},
        "t1", None)
    assert out == {}


async def test_the_gate_refuses_in_the_project_language(tmp_path):
    b = _builder(tmp_path, None, language="en")
    out = await b._on_pre_tool_use(
        {"tool_name": "Bash", "tool_input": {"command": "pkill -f node"}},
        "t1", None)
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "Refused" in reason
