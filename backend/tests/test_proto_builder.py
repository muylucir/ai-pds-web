# backend/tests/test_proto_builder.py — ported from harness/tests/test_sdk_driver.py.
# The driver logic is unchanged; only its home and constructor moved.
from __future__ import annotations

from pathfinder.models import AgentEvent
from pathfinder.proto.builder import PrototypeBuilder
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
    assert b.drain_queue() == [
        AgentEvent(kind="file_changed", path="prototype/app.js")]


async def test_post_tool_hook_rejects_escape(tmp_path):
    b = _builder(tmp_path, FakeSdkClient())
    await b._on_post_tool_use(
        {"tool_name": "Write", "tool_input": {"file_path": "/etc/passwd"}},
        "toolu_1", None)
    events = b.drain_queue()
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

def _real_options(**kw):
    """Capture the ClaudeAgentOptions the default factory hands the SDK."""
    import claude_agent_sdk
    import pathfinder.proto.builder as bmod

    captured = {}

    class Spy:
        def __init__(self, options=None):
            captured["options"] = options

    original, claude_agent_sdk.ClaudeSDKClient = claude_agent_sdk.ClaudeSDKClient, Spy
    try:
        builder = bmod.PrototypeBuilder(
            workspace="/tmp/ws", config_dir="/opt/pathfinder/proto-config",
            session_id="11111111-2222-3333-4444-555555555555", resume=False, **kw)
        builder._factory()
    finally:
        claude_agent_sdk.ClaudeSDKClient = original
    return captured["options"]


def test_skills_are_enabled_for_the_whole_config_dir():
    """`skills="all"` is what makes a committed proto-config/skills/<name>/
    SKILL.md take effect with no code change. It is only safe because
    CLAUDE_CONFIG_DIR is OURS -- with the default ~/.claude this would enable
    whatever the host user happens to have installed."""
    assert _real_options().skills == "all"


def test_setting_sources_stay_open_so_skills_can_be_discovered():
    """`skills` cannot find anything if the filesystem sources are closed;
    "user" here means our config dir, not the operator's home."""
    assert _real_options().setting_sources == ["user", "project"]


def test_config_dir_is_always_injected():
    """The guard against the bundled binary falling back to the backend user's
    personal ~/.claude (their own skills/agents/CLAUDE.md leaking into every
    build). There is exactly one options site, so this pins it."""
    assert _real_options().env["CLAUDE_CONFIG_DIR"] == "/opt/pathfinder/proto-config"


def test_we_do_not_restrict_the_agents_own_tools():
    """The build agent needs Bash/Write/Edit. We must never populate
    allowed_tools ourselves -- the SDK appends "Skill" to it for skills="all",
    and anything we added would narrow that list."""
    assert _real_options().allowed_tools == []


def test_file_checkpointing_stays_off_so_session_store_is_legal():
    """The SDK raises ValueError when session_store is combined with
    enable_file_checkpointing -- that would break durable transcripts."""
    assert _real_options().enable_file_checkpointing is False
