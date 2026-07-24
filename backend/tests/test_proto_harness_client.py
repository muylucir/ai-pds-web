# backend/tests/test_proto_harness_client.py
from __future__ import annotations
import json
import httpx
import pytest
from pathfinder.proto.harness_client import HarnessClient


def _sse_body(events: list[dict]) -> bytes:
    lines = []
    for ev in events:
        lines.append(f"data: {json.dumps(ev)}".encode("utf-8"))
        lines.append(b"")
    return b"\n".join(lines) + b"\n"


def _client(handler) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, base_url="http://vm")


async def test_send_message_streams_ordered_events():
    events = [
        {"kind": "status", "text": "working", "path": None, "payload": None},
        {"kind": "message", "text": "hi there", "path": None, "payload": None},
        {"kind": "done", "text": None, "path": None, "payload": None},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/message"
        assert json.loads(request.content) == {"text": "go"}
        return httpx.Response(200, content=_sse_body(events),
                              headers={"content-type": "text/event-stream"})

    async with _client(handler) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        seen = [e async for e in hc.send_message("go")]
    assert [e.kind for e in seen] == ["status", "message", "done"]
    assert seen[1].text == "hi there"


async def test_send_message_stops_on_error_frame():
    events = [
        {"kind": "status", "text": "working", "path": None, "payload": None},
        {"kind": "error", "text": "boom", "path": None, "payload": None},
        {"kind": "message", "text": "should not be seen", "path": None, "payload": None},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse_body(events),
                              headers={"content-type": "text/event-stream"})

    async with _client(handler) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        seen = [e async for e in hc.send_message("go")]
    assert [e.kind for e in seen] == ["status", "error"]
    assert seen[-1].text == "boom"


async def test_send_message_has_no_session_field():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, content=_sse_body([{"kind": "done", "text": None,
                                     "path": None, "payload": None}]),
            headers={"content-type": "text/event-stream"})

    async with _client(handler) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        [e async for e in hc.send_message("hi")]
    assert captured["body"] == {"text": "hi"}


async def test_send_answers_returns_true_on_204():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/answers"
        assert json.loads(request.content) == {
            "interrupt_id": "i-1", "answers": {"1": "A"},
        }
        return httpx.Response(204)

    async with _client(handler) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        assert await hc.send_answers("i-1", {"1": "A"}) is True


async def test_send_answers_returns_false_on_409():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409)

    async with _client(handler) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        assert await hc.send_answers("i-1", {"1": "A"}) is False


async def test_send_answers_raises_on_other_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with _client(handler) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        with pytest.raises(httpx.HTTPStatusError):
            await hc.send_answers("i-1", {"1": "A"})


async def test_interrupt_posts_and_raises_for_status():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(202)

    async with _client(handler) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        await hc.interrupt()
    assert calls == ["/interrupt"]


async def test_interrupt_raises_on_error_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with _client(handler) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        with pytest.raises(httpx.HTTPStatusError):
            await hc.interrupt()


async def test_pending_sends_empty_json_object_body():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json={"pending": '{"interrupt_id":"i-1"}'})

    async with _client(handler) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        result = await hc.pending()
    assert captured["body"] == b"{}"
    assert result == '{"interrupt_id":"i-1"}'


async def test_pending_none_when_no_question_pending():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"pending": None})

    async with _client(handler) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        assert await hc.pending() is None


async def test_read_write_file_roundtrip():
    store = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            store[request.url.path] = request.content
            return httpx.Response(204)
        if request.method == "GET" and request.url.path.startswith("/files/"):
            if request.url.path not in store:
                return httpx.Response(404)
            return httpx.Response(200, content=store[request.url.path])
        raise AssertionError(f"unexpected request {request.method} {request.url.path}")

    async with _client(handler) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        await hc.write_file("aiplc-docs/x.md", "content")
        assert await hc.read_file("aiplc-docs/x.md") == "content"


async def test_read_missing_file_raises_filenotfound():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async with _client(handler) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        with pytest.raises(FileNotFoundError):
            await hc.read_file("aiplc-docs/missing.md")


async def test_list_files_returns_matching_paths():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/files"
        assert request.url.params["glob"] == "aiplc-docs/*-questions.md"
        return httpx.Response(200, json=["aiplc-docs/a-questions.md",
                                          "aiplc-docs/b-questions.md"])

    async with _client(handler) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        found = await hc.list_files("aiplc-docs/*-questions.md")
    assert found == ["aiplc-docs/a-questions.md", "aiplc-docs/b-questions.md"]


async def test_heartbeat_true_on_healthy():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    async with _client(handler) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        assert await hc.heartbeat() is True


async def test_heartbeat_false_on_transport_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async with _client(handler) as http:
        hc = HarnessClient(base_url="http://vm", http=http)
        assert await hc.heartbeat() is False


async def test_headers_merged_into_every_request():
    seen_headers = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers.get("x-aws-proxy-auth"))
        return httpx.Response(200, json={"ok": True})

    async with _client(handler) as http:
        hc = HarnessClient(base_url="http://vm", http=http,
                           headers={"X-aws-proxy-auth": "jwe-abc"})
        await hc.heartbeat()
    assert seen_headers == ["jwe-abc"]
