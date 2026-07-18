import httpx
import pytest
from pathfinder.sandbox.harness import HarnessClient
from fakes.harness_app import build_fake_harness_app


class _Recorder(httpx.AsyncBaseTransport):
    """Wraps an ASGI transport, capturing the headers of every request."""
    def __init__(self, app):
        self._inner = httpx.ASGITransport(app=app)
        self.seen: list[httpx.Headers] = []

    async def handle_async_request(self, request):
        self.seen.append(request.headers)
        return await self._inner.handle_async_request(request)


def _client_with_recorder(app):
    rec = _Recorder(app)
    return httpx.AsyncClient(transport=rec, base_url="http://vm"), rec


async def test_auth_header_attached_to_message_stream():
    app = build_fake_harness_app([
        {"kind": "message", "text": "hi", "path": None},
        {"kind": "done", "text": None, "path": None},
    ])
    http, rec = _client_with_recorder(app)
    async with http:
        hc = HarnessClient(base_url="http://vm", http=http,
                           headers={"X-aws-proxy-auth": "tok-123"})
        _ = [e async for e in hc.send_message("go")]
    assert rec.seen, "no request captured"
    assert rec.seen[0]["X-aws-proxy-auth"] == "tok-123"


async def test_auth_header_attached_to_file_and_health_ops():
    app = build_fake_harness_app()
    http, rec = _client_with_recorder(app)
    async with http:
        hc = HarnessClient(base_url="http://vm", http=http,
                           headers={"X-aws-proxy-auth": "tok-xyz"})
        await hc.write_file("aiplc-docs/a.md", "x")
        await hc.read_file("aiplc-docs/a.md")
        await hc.list_files("aiplc-docs/*")
        await hc.heartbeat()
    assert len(rec.seen) == 4
    for h in rec.seen:
        assert h["X-aws-proxy-auth"] == "tok-xyz"


async def test_no_headers_arg_still_works_and_sends_none():
    app = build_fake_harness_app()
    http, rec = _client_with_recorder(app)
    async with http:
        hc = HarnessClient(base_url="http://vm", http=http)  # 2-arg, unchanged
        await hc.heartbeat()
    assert "X-aws-proxy-auth" not in rec.seen[0]
