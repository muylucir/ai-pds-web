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

_SID = "11111111-2222-3333-4444-555555555555"


def _real_options(resume=False, **kw):
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
            session_id=_SID, resume=resume, **kw)
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
    from pathfinder.proto.builder import PrototypeBuilder

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
