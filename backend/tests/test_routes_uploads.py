import asyncio
import io
from fastapi.testclient import TestClient
import pathfinder.app as app_module

client = TestClient(app_module.app)

def _local_project(monkeypatch, pid):
    import tempfile
    from pathlib import Path
    from pathfinder.sandbox.local import LocalSandbox
    async def make(project_id):
        sb = LocalSandbox(root=Path(tempfile.mkdtemp()))
        await sb.start()
        return sb
    monkeypatch.setattr(app_module, "make_sandbox", make)
    client.post("/projects", json={"project_id": pid})

def test_upload_md_saved_to_uploads_prefix(monkeypatch):
    _local_project(monkeypatch, "u1")
    r = client.post("/projects/u1/uploads",
                    files={"file": ("의견.md", io.BytesIO("# 의견".encode()), "text/markdown")})
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == "uploads/의견.md" and body["truncated"] is False
    # 저장 확인: 같은 sandbox의 read_file 경유 (files API가 없으므로 questions 경로 재사용 불가 →
    # workspace registry로 직접). asyncio.get_event_loop().run_until_complete는 이 파일이 이미
    # 쓰고 있는 관례(test_routes_artifacts.py의 _create_and_seed)와 동일 — conftest의
    # _ensure_event_loop 오토유즈 픽스처가 루프를 보장한다.
    ws = app_module.registry.get("u1")
    assert asyncio.get_event_loop().run_until_complete(
        ws.sandbox.read_file("uploads/의견.md")) == "# 의견"

def test_upload_collision_gets_suffix(monkeypatch):
    _local_project(monkeypatch, "u2")
    for _ in range(2):
        r = client.post("/projects/u2/uploads",
                        files={"file": ("a.md", io.BytesIO(b"x"), "text/markdown")})
    assert r.json()["path"] == "uploads/a-2.md"

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
