import httpx
import pytest
from pathfinder.sandbox.harness import HarnessClient
from fakes.harness_app import build_fake_harness_app

def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://vm")

async def test_send_message_streams_ordered_events():
    app = build_fake_harness_app([
        {"kind": "status", "text": "working", "path": None},
        {"kind": "message", "text": "hi there", "path": None},
        {"kind": "done", "text": None, "path": None},
    ])
    async with _client(app) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        events = [e async for e in hc.send_message("go")]
    assert [e.kind for e in events] == ["status", "message", "done"]
    assert events[1].text == "hi there"

async def test_send_message_stops_on_error_frame():
    app = build_fake_harness_app([
        {"kind": "status", "text": "working", "path": None},
        {"kind": "error", "text": "boom", "path": None},
    ])
    async with _client(app) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        events = [e async for e in hc.send_message("go")]
    assert events[-1].kind == "error"
    assert events[-1].text == "boom"

async def test_file_write_read_roundtrip():
    app = build_fake_harness_app()
    async with _client(app) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        await hc.write_file("aiplc-docs/x.md", "content")
        assert await hc.read_file("aiplc-docs/x.md") == "content"

async def test_read_missing_file_raises_filenotfound():
    app = build_fake_harness_app()
    async with _client(app) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        with pytest.raises(FileNotFoundError):
            await hc.read_file("aiplc-docs/missing.md")

async def test_list_files_returns_matching_paths():
    app = build_fake_harness_app()
    async with _client(app) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        await hc.write_file("aiplc-docs/a-questions.md", "x")
        await hc.write_file("aiplc-docs/b-questions.md", "y")
        await hc.write_file("aiplc-docs/audit.md", "z")
        found = await hc.list_files("aiplc-docs/*-questions.md")
    assert found == ["aiplc-docs/a-questions.md", "aiplc-docs/b-questions.md"]

async def test_heartbeat_true_on_healthy():
    app = build_fake_harness_app()
    async with _client(app) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        assert await hc.heartbeat() is True
