import asyncio
import pytest
from claude_driver import AgentEvent, translate, ClaudeDriver

WS = "/workspace"


def test_translate_assistant_text_to_message():
    obj = {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}
    assert translate(obj, WS) == [AgentEvent(kind="message", text="hi")]


def test_translate_write_tool_to_file_changed_relative():
    obj = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Write",
         "input": {"file_path": "/workspace/aiplc-docs/audit.md", "content": "x"}}]}}
    assert translate(obj, WS) == [AgentEvent(kind="file_changed", path="aiplc-docs/audit.md")]


def test_translate_other_tool_to_status_with_name():
    obj = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}}
    assert translate(obj, WS) == [AgentEvent(kind="status", text="Bash")]


def test_translate_result_to_done():
    assert translate({"type": "result", "subtype": "success"}, WS) == [AgentEvent(kind="done")]


def test_translate_system_framing_is_none():
    assert translate({"type": "system", "subtype": "init"}, WS) == []


def test_translate_write_tool_absolute_traversal_escapes_workspace():
    # /workspace/../etc/passwd: PurePosixPath.relative_to doesn't normalize,
    # so a naive relativize would yield "../etc/passwd" — an escape. Must be
    # rejected: no file_changed, no path echo.
    obj = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Write",
         "input": {"file_path": "/workspace/../etc/passwd", "content": "x"}}]}}
    events = translate(obj, WS)
    assert events == [AgentEvent(kind="status", text="file outside workspace ignored")]
    assert events[0].path is None


def test_translate_write_tool_embedded_dotdot_escapes_workspace():
    obj = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Write",
         "input": {"file_path": "/workspace/aiplc-docs/../../etc/passwd", "content": "x"}}]}}
    events = translate(obj, WS)
    assert events == [AgentEvent(kind="status", text="file outside workspace ignored")]
    assert events[0].path is None


def test_translate_text_and_tool_use_in_one_message_both_emitted():
    obj = {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "작성 중"},
        {"type": "tool_use", "name": "Write",
         "input": {"file_path": "/workspace/aiplc-docs/notes.md", "content": "y"}}]}}
    events = translate(obj, WS)
    assert events == [
        AgentEvent(kind="message", text="작성 중"),
        AgentEvent(kind="file_changed", path="aiplc-docs/notes.md"),
    ]


def test_translate_parallel_tool_use_blocks_both_emitted():
    obj = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
        {"type": "tool_use", "name": "Bash", "input": {"command": "pwd"}}]}}
    events = translate(obj, WS)
    assert events == [
        AgentEvent(kind="status", text="Bash"),
        AgentEvent(kind="status", text="Bash"),
    ]


async def test_run_yields_events_ending_in_done(stub_claude):
    driver = ClaudeDriver(workspace=WS, claude_bin=stub_claude("basic_turn.jsonl"))
    events = [e async for e in driver.run("go", continue_session=False)]
    assert [e.kind for e in events] == ["message", "file_changed", "status", "done"]
    assert events[1].path == "aiplc-docs/audit.md"


async def test_run_translates_all_blocks_of_multi_block_fixture(stub_claude):
    driver = ClaudeDriver(workspace=WS, claude_bin=stub_claude("multi_block_turn.jsonl"))
    events = [e async for e in driver.run("go", continue_session=False)]
    assert [e.kind for e in events] == ["message", "file_changed", "status", "status", "done"]
    assert events[1].path == "aiplc-docs/notes.md"


async def test_run_nonzero_exit_yields_error(stub_claude):
    driver = ClaudeDriver(workspace=WS, claude_bin=stub_claude("", exit_code=3))
    events = [e async for e in driver.run("go", continue_session=False)]
    assert events[-1].kind == "error"


async def test_run_passes_continue_flag(stub_claude, monkeypatch):
    captured = {}
    driver = ClaudeDriver(workspace=WS, claude_bin=stub_claude())

    orig = ClaudeDriver._argv
    def spy(self, text, continue_session):
        argv = orig(self, text, continue_session)
        captured["argv"] = argv
        return argv
    monkeypatch.setattr(ClaudeDriver, "_argv", spy)

    _ = [e async for e in driver.run("go", continue_session=True)]
    assert "--continue" in captured["argv"]
    _ = [e async for e in driver.run("go", continue_session=False)]
    assert "--continue" not in captured["argv"]


async def test_run_large_stderr_does_not_deadlock(stub_claude):
    # >64KB (a typical OS pipe buffer size) written to stderr before any
    # stdout: if stderr isn't drained concurrently, the child blocks on the
    # stderr write syscall and stdout (and therefore run()) never completes.
    driver = ClaudeDriver(
        workspace=WS,
        claude_bin=stub_claude("basic_turn.jsonl", stderr_bytes=70_000),
    )
    events = await asyncio.wait_for(
        _collect(driver.run("go", continue_session=False)), timeout=10
    )
    assert [e.kind for e in events] == ["message", "file_changed", "status", "done"]
    # None of the discarded stderr filler leaks into any event's text.
    assert all(e.text is None or "E" * 100 not in e.text for e in events)


async def test_run_logs_stderr_tail_on_nonzero_exit_but_not_in_event(stub_claude, caplog):
    # A failed turn must be debuggable: the stderr tail is logged server-side,
    # but the user-facing event stays exit-code-only (no raw stderr leak).
    import logging
    driver = ClaudeDriver(
        workspace=WS,
        claude_bin=stub_claude("basic_turn.jsonl", exit_code=3, stderr_bytes=0,
                               stderr_text="ANTHROPIC_AUTH boom: creds bad"),
    )
    with caplog.at_level(logging.ERROR, logger="harness.driver"):
        events = await _collect(driver.run("go", continue_session=False))
    assert events[-1].kind == "error"
    assert events[-1].text == "claude exited 3"
    assert "ANTHROPIC_AUTH boom" not in (events[-1].text or "")  # not in the event
    assert "ANTHROPIC_AUTH boom" in caplog.text                   # but IS in the log


async def _collect(aiter):
    return [e async for e in aiter]


async def test_run_abandoned_generator_kills_and_reaps_subprocess(
    hanging_stub_claude, monkeypatch
):
    # Reliable technique: intercept asyncio.create_subprocess_exec to capture
    # the real asyncio.subprocess.Process the driver spawns, so the test can
    # assert on it directly rather than guessing at OS-level process
    # liveness. The stub script prints one line then sleeps for 60s instead
    # of exiting -- if run()'s cleanup didn't kill it, awaiting proc.wait()
    # here would hang for the full 60s (caught by the outer wait_for).
    captured = {}
    orig_create = asyncio.create_subprocess_exec

    async def spy_create(*args, **kwargs):
        proc = await orig_create(*args, **kwargs)
        captured["proc"] = proc
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy_create)

    driver = ClaudeDriver(workspace=WS, claude_bin=hanging_stub_claude)
    gen = driver.run("go", continue_session=False)
    first = await gen.__anext__()
    assert first.kind == "message"

    await gen.aclose()

    proc = captured["proc"]
    # If cleanup killed+reaped it, this returns immediately with a non-None
    # (killed) returncode instead of hanging until the stub's 60s sleep ends.
    await asyncio.wait_for(proc.wait(), timeout=5)
    assert proc.returncode is not None
    assert proc.returncode != 0
