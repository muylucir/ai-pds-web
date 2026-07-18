import pytest
from claude_driver import AgentEvent, translate, ClaudeDriver

WS = "/workspace"


def test_translate_assistant_text_to_message():
    obj = {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}
    ev = translate(obj, WS)
    assert ev == AgentEvent(kind="message", text="hi")


def test_translate_write_tool_to_file_changed_relative():
    obj = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Write",
         "input": {"file_path": "/workspace/aiplc-docs/audit.md", "content": "x"}}]}}
    ev = translate(obj, WS)
    assert ev == AgentEvent(kind="file_changed", path="aiplc-docs/audit.md")


def test_translate_other_tool_to_status_with_name():
    obj = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}}
    assert translate(obj, WS) == AgentEvent(kind="status", text="Bash")


def test_translate_result_to_done():
    assert translate({"type": "result", "subtype": "success"}, WS) == AgentEvent(kind="done")


def test_translate_system_framing_is_none():
    assert translate({"type": "system", "subtype": "init"}, WS) is None


async def test_run_yields_events_ending_in_done(stub_claude):
    driver = ClaudeDriver(workspace=WS, claude_bin=stub_claude("basic_turn.jsonl"))
    events = [e async for e in driver.run("go", continue_session=False)]
    assert [e.kind for e in events] == ["message", "file_changed", "status", "done"]
    assert events[1].path == "aiplc-docs/audit.md"


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
