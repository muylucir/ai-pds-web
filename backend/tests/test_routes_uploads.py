import asyncio
import io
import re
from fastapi.testclient import TestClient
import aipds.app as app_module
from aipds.workspace import Workspace
from fakes.fake_runner import FakeRunner

client = TestClient(app_module.app)

_KEY_RE = re.compile(r"^uploads/[0-9a-f]{8}/(.+)$")

def _local_project(monkeypatch, pid):
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "")  # offline: no durable manifest write
    async def make(project_id):
        return Workspace(FakeRunner())
    monkeypatch.setattr(app_module, "make_workspace", make)
    client.post("/projects", json={"project_id": pid})

def test_upload_md_saved_under_a_uuid_directory(monkeypatch):
    _local_project(monkeypatch, "u1")
    r = client.post("/projects/u1/uploads",
                    files={"file": ("의견.md", io.BytesIO("# 의견".encode()), "text/markdown")})
    assert r.status_code == 200
    body = r.json()
    m = _KEY_RE.match(body["path"])
    assert m and m.group(1) == "의견.md.md"
    assert body["truncated"] is False
    # 저장 확인: 같은 runner의 read_file 경유 (files API가 없으므로 workspace registry로 직접).
    # conftest의 _ensure_event_loop 오토유즈 픽스처가 루프를 보장한다.
    ws = app_module.registry.get("u1")
    assert asyncio.get_event_loop().run_until_complete(
        ws.runner.read_file(body["path"])) == "# 의견"

def test_same_name_uploads_do_not_overwrite(monkeypatch):
    """The regression: the old list-then-write path let two uploads of one
    name land on the same key, and the later write silently deleted the
    earlier file."""
    _local_project(monkeypatch, "u2")
    paths = []
    for _ in range(2):
        r = client.post("/projects/u2/uploads",
                        files={"file": ("a.md", io.BytesIO(b"x"), "text/markdown")})
        paths.append(r.json()["path"])
    assert paths[0] != paths[1]

    ws = app_module.registry.get("u2")
    loop = asyncio.get_event_loop()
    for p in paths:
        assert loop.run_until_complete(ws.runner.read_file(p)) == "x"

def test_upload_rejects_big_and_unsupported(monkeypatch):
    _local_project(monkeypatch, "u3")
    big = io.BytesIO(b"0" * (5 * 1024 * 1024 + 1))
    assert client.post("/projects/u3/uploads",
                       files={"file": ("big.txt", big, "text/plain")}).status_code == 413
    assert client.post("/projects/u3/uploads",
                       files={"file": ("run.exe", io.BytesIO(b"MZ"), "application/x-msdownload")}
                       ).status_code == 415

def test_upload_unknown_project_404():
    assert client.post("/projects/ghost/uploads",
                       files={"file": ("a.md", io.BytesIO(b"x"), "text/markdown")}).status_code == 404

def test_upload_corrupt_xlsx_415_not_500(monkeypatch):
    # Reviewer-flagged gap: a valid extension with garbage bytes must 415, not
    # 500 (openpyxl's zipfile.BadZipFile must never leak past convert()).
    _local_project(monkeypatch, "u4")
    r = client.post("/projects/u4/uploads",
                    files={"file": ("bad.xlsx", io.BytesIO(b"not a real xlsx"),
                                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 415

def test_upload_spoofed_content_length_rejected_before_body_read(monkeypatch):
    # Reviewer-flagged gap: a Content-Length far beyond the 5MB cap should be
    # rejected from the header alone, before the body is spooled/read. This
    # exercises the pre-check branch directly (TestClient/httpx honors an
    # explicit Content-Length override even though the body is small) --
    # the post-read len(data) check (test_upload_rejects_big_and_unsupported)
    # remains the authoritative fallback for a truthful Content-Length.
    _local_project(monkeypatch, "u5")
    r = client.post("/projects/u5/uploads",
                    files={"file": ("a.txt", io.BytesIO(b"x"), "text/plain")},
                    headers={"Content-Length": str(6 * 1024 * 1024)})
    assert r.status_code == 413
