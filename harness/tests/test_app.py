from pathlib import Path

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


async def test_files_list_double_star_glob_includes_top_level_and_nested(tmp_path):
    drv = FakeDriver(str(tmp_path))
    async with _client(build_app(drv, str(tmp_path))) as http:
        await http.put("/files/aiplc-docs/audit.md", content=b"top")
        await http.put("/files/aiplc-docs/sub/nested.md", content=b"nested")
        r = await http.get("/files", params={"glob": "aiplc-docs/**/*"})
    assert r.json() == ["aiplc-docs/audit.md", "aiplc-docs/sub/nested.md"]


async def test_files_list_double_star_glob_catches_top_level_index_html(tmp_path):
    drv = FakeDriver(str(tmp_path))
    async with _client(build_app(drv, str(tmp_path))) as http:
        await http.put("/files/prototype/index.html", content=b"<html></html>")
        r = await http.get("/files", params={"glob": "prototype/**/*"})
    assert r.json() == ["prototype/index.html"]


async def test_get_file_binary_content_no_crash_replacement_chars(tmp_path):
    drv = FakeDriver(str(tmp_path))
    p = Path(tmp_path) / "aiplc-docs" / "bin.dat"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\xff\xfe\x00binary\x80")
    async with _client(build_app(drv, str(tmp_path))) as http:
        r = await http.get("/files/aiplc-docs/bin.dat")
    assert r.status_code == 200
    assert "�" in r.text


async def test_get_file_path_traversal_rejected(tmp_path):
    # httpx normalizes literal "../" dot-segments client-side before the
    # request goes out (RFC 3986), so a raw client (curl, or the drill
    # scripts, which talk to the harness directly and bypass
    # MicroVMSandbox's reject_unsafe) is simulated with percent-encoded
    # dots, which travel over the wire untouched and land in
    # request.path_params["path"] as a literal "..".
    drv = FakeDriver(str(tmp_path))
    async with _client(build_app(drv, str(tmp_path))) as http:
        r = await http.get("/files/%2e%2e/etc/hostname")
    assert r.status_code == 400


async def test_put_file_path_traversal_rejected(tmp_path):
    drv = FakeDriver(str(tmp_path))
    async with _client(build_app(drv, str(tmp_path))) as http:
        r = await http.put("/files/a/%2e%2e/%2e%2e/tmp/x", content=b"pwn")
    assert r.status_code == 400


async def test_put_file_leading_slash_rejected(tmp_path):
    # A leading "/" in the {path:path} match (e.g. "//etc/cron.d/x") makes
    # `ws / rel` discard the workspace prefix entirely (pathlib treats an
    # absolute right-hand operand as a full replacement), landing outside
    # the workspace. Confinement must catch this too, not just "..".
    drv = FakeDriver(str(tmp_path))
    async with _client(build_app(drv, str(tmp_path))) as http:
        r = await http.put("/files//etc/cron.d/pwn", content=b"pwn")
    assert r.status_code == 400


async def test_files_legit_nested_path_still_works(tmp_path):
    drv = FakeDriver(str(tmp_path))
    async with _client(build_app(drv, str(tmp_path))) as http:
        put_resp = await http.put("/files/aiplc-docs/sub/deep/a.md", content=b"deep")
        assert put_resp.status_code in (200, 204)
        got = await http.get("/files/aiplc-docs/sub/deep/a.md")
    assert got.status_code == 200 and got.text == "deep"
