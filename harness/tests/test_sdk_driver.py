import pytest
from sdk_driver import SdkDriver
from tests.fake_sdk import (FakeSdkClient, AssistantMessage, TextBlock,
                            ToolUseBlock, ResultMessage)

async def collect(driver, text="go"):
    return [ev async for ev in driver.run(text)]

@pytest.mark.asyncio
async def test_text_and_result_translate(tmp_path):
    client = FakeSdkClient(script=[
        AssistantMessage(content=[TextBlock(text="working on it")]),
        ResultMessage(subtype="success"),
    ])
    d = SdkDriver(str(tmp_path), client_factory=lambda: client)
    events = await collect(d)
    kinds = [(e.kind, e.text) for e in events]
    assert ("message", "working on it") in kinds
    assert events[-1].kind == "done"
    assert client.queries == ["go"]

@pytest.mark.asyncio
async def test_tool_use_status_deduped(tmp_path):
    client = FakeSdkClient(script=[
        AssistantMessage(content=[ToolUseBlock(id="1", name="Bash", input={}),
                                  ToolUseBlock(id="2", name="Bash", input={})]),
        AssistantMessage(content=[ToolUseBlock(id="3", name="Write",
                                               input={"file_path": "x"})]),
        ResultMessage(),
    ])
    d = SdkDriver(str(tmp_path), client_factory=lambda: client)
    events = await collect(d)
    statuses = [e.text for e in events if e.kind == "status"]
    assert statuses == ["Bash", "Write"]

@pytest.mark.asyncio
async def test_client_error_yields_sanitized_error(tmp_path):
    class Boom(FakeSdkClient):
        async def receive_response(self):
            raise RuntimeError("AWS_SECRET=xyz leaked")
            yield  # pragma: no cover
    d = SdkDriver(str(tmp_path), client_factory=lambda: Boom())
    events = await collect(d)
    assert events[-1].kind == "error"
    assert "xyz" not in (events[-1].text or "")

@pytest.mark.asyncio
async def test_second_turn_reuses_connected_client(tmp_path):
    client = FakeSdkClient(script=[ResultMessage()])
    d = SdkDriver(str(tmp_path), client_factory=lambda: client)
    await collect(d, "one")
    await collect(d, "two")
    assert client.queries == ["one", "two"]

@pytest.mark.asyncio
async def test_turn_already_in_progress(tmp_path):
    client = FakeSdkClient(script=[ResultMessage()])
    d = SdkDriver(str(tmp_path), client_factory=lambda: client)
    d._turn_active = True
    events = await collect(d)
    assert events[0].kind == "error"
    assert "in progress" in events[0].text

@pytest.mark.asyncio
async def test_post_tool_hook_emits_file_changed(tmp_path):
    d = SdkDriver(str(tmp_path), client_factory=lambda: FakeSdkClient())
    out = await d._on_post_tool_use(
        {"tool_name": "Write",
         "tool_input": {"file_path": f"{tmp_path}/prototype/app.js"}},
        "toolu_1", None)
    assert out == {}
    assert [e.kind for e in d.drain_queue()] == ["file_changed"]
    assert d._queue == []

@pytest.mark.asyncio
async def test_post_tool_hook_rejects_escape(tmp_path):
    d = SdkDriver(str(tmp_path), client_factory=lambda: FakeSdkClient())
    await d._on_post_tool_use(
        {"tool_name": "Write", "tool_input": {"file_path": "/etc/passwd"}},
        "toolu_1", None)
    evs = d.drain_queue()
    assert evs[0].kind == "status" and "outside workspace" in evs[0].text
