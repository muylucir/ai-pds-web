import httpx
import pytest
from app import build_app
from claude_driver import AgentEvent


class FakeDriver:
    def __init__(self, workspace):
        self.workspace = workspace
        self.calls: list[bool] = []
        self.files: dict[str, str] = {}

    async def run(self, text, *, continue_session):
        self.calls.append(continue_session)
        yield AgentEvent(kind="message", text=f"echo:{text}")
        yield AgentEvent(kind="done")


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://vm")


async def test_message_streams_sse_events():
    drv = FakeDriver("/workspace")
    async with _client(build_app(drv, "/workspace")) as http:
        lines = []
        async with http.stream("POST", "/message", json={"text": "go"}) as r:
            assert r.status_code == 200
            async for ln in r.aiter_lines():
                if ln.startswith("data:"):
                    lines.append(ln)
    assert any('"kind": "message"' in l or '"kind":"message"' in l for l in lines)
    assert any('"done"' in l for l in lines)


async def test_first_turn_no_continue_then_continue():
    drv = FakeDriver("/workspace")
    async with _client(build_app(drv, "/workspace")) as http:
        for _ in range(2):
            async with http.stream("POST", "/message", json={"text": "go"}) as r:
                async for _ln in r.aiter_lines():
                    pass
    assert drv.calls == [False, True]


async def test_files_put_get_roundtrip_and_404(tmp_path):
    drv = FakeDriver(str(tmp_path))
    async with _client(build_app(drv, str(tmp_path))) as http:
        assert (await http.get("/files/aiplc-docs/missing.md")).status_code == 404
        assert (await http.put("/files/aiplc-docs/a.md", content=b"hello")).status_code in (200, 204)
        got = await http.get("/files/aiplc-docs/a.md")
        assert got.status_code == 200 and got.text == "hello"


async def test_files_list_glob(tmp_path):
    drv = FakeDriver(str(tmp_path))
    async with _client(build_app(drv, str(tmp_path))) as http:
        await http.put("/files/aiplc-docs/a-questions.md", content=b"x")
        await http.put("/files/aiplc-docs/audit.md", content=b"y")
        r = await http.get("/files", params={"glob": "aiplc-docs/*-questions.md"})
    assert r.json() == ["aiplc-docs/a-questions.md"]


async def test_health_ok(tmp_path):
    drv = FakeDriver(str(tmp_path))
    async with _client(build_app(drv, str(tmp_path))) as http:
        r = await http.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True
