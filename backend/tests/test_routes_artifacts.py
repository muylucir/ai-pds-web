# backend/tests/test_routes_artifacts.py
import asyncio
from pathlib import Path
from urllib.parse import quote
from fastapi.testclient import TestClient
import pathfinder.app as app_module
from pathfinder.app import app, registry
from pathfinder.workspace import Workspace
from fakes.fake_runner import FakeRunner

FIX = Path(__file__).parent / "fixtures"
client = TestClient(app)


def _install(monkeypatch):
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "")  # offline: no durable manifest write
    async def make(project_id):
        return Workspace(FakeRunner())
    monkeypatch.setattr(app_module, "make_workspace", make)


def _create_and_seed(monkeypatch, pid):
    _install(monkeypatch)
    assert client.post("/projects", json={"project_id": pid}).status_code == 200
    ws = registry.get(pid)
    async def seed():
        await ws.runner.write_file("aiplc-docs/aiplc-state.md",
            (FIX / "aiplc-state.md").read_text(encoding="utf-8"))
        await ws.runner.write_file("aiplc-docs/strategy-questions.md",
            (FIX / "strategy-questions.md").read_text(encoding="utf-8"))
    asyncio.get_event_loop().run_until_complete(seed())

def _seeded_project(monkeypatch, pid, files):
    _install(monkeypatch)
    assert client.post("/projects", json={"project_id": pid}).status_code == 200
    ws = registry.get(pid)
    async def seed():
        for path, content in files.items():
            await ws.runner.write_file(path, content)
    asyncio.get_event_loop().run_until_complete(seed())


def test_create_project_conflict(monkeypatch):
    _install(monkeypatch)
    client.post("/projects", json={"project_id": "dup"})
    r = client.post("/projects", json={"project_id": "dup"})
    assert r.status_code == 409

def test_get_state_route(monkeypatch):
    _create_and_seed(monkeypatch, "proj-state")
    r = client.get("/projects/proj-state/state")
    assert r.status_code == 200
    assert r.json()["project_type"] == "Greenfield"

def test_get_questions_route(monkeypatch):
    _create_and_seed(monkeypatch, "proj-q")
    r = client.get("/projects/proj-q/questions/aiplc-docs/strategy-questions.md")
    assert r.status_code == 200
    assert len(r.json()["questions"]) == 13

def test_unknown_project_404():
    assert client.get("/projects/nope/state").status_code == 404

def test_read_artifact_returns_content_and_guards_prefix(monkeypatch):
    _create_and_seed(monkeypatch, "proj-files")
    ws = registry.get("proj-files")
    asyncio.get_event_loop().run_until_complete(
        ws.runner.write_file("aiplc-docs/discovery/prfaq.md", "# PR/FAQ\n\nContent."))

    r = client.get("/projects/proj-files/files/aiplc-docs/discovery/prfaq.md")
    assert r.status_code == 200
    assert r.json()["content"].startswith("# PR")

    assert client.get("/projects/proj-files/files/uploads/x.md").status_code == 403
    assert client.get("/projects/proj-files/files/aiplc-docs/none.md").status_code == 404


import io
import zipfile


def test_archive_returns_zip_of_artifacts(monkeypatch):
    pid = "zip1"
    _seeded_project(monkeypatch, pid, {
        "aiplc-docs/discovery/discovery-document.md": "# Doc",
        "aiplc-docs/audit.md": "# Audit",
        "uploads/raw.md": "NOT INCLUDED",          # 산출물 아님
    })
    r = client.get(f"/projects/{pid}/artifacts/archive")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert f'filename="{pid}-artifacts.zip"' in r.headers["content-disposition"]
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(zf.namelist()) == ["aiplc-docs/audit.md", "aiplc-docs/discovery/discovery-document.md"]
    assert zf.read("aiplc-docs/discovery/discovery-document.md").decode() == "# Doc"


def test_archive_404_when_no_artifacts(monkeypatch):
    pid = "zip-empty"
    _seeded_project(monkeypatch, pid, {})
    assert client.get(f"/projects/{pid}/artifacts/archive").status_code == 404


def test_archive_404_unknown_project():
    assert client.get("/projects/zip-ghost/artifacts/archive").status_code == 404


def test_archive_korean_pid_does_not_500(monkeypatch):
    pid = "한글프로젝트"
    _seeded_project(monkeypatch, pid, {
        "aiplc-docs/audit.md": "# Audit",
    })
    r = client.get(f"/projects/{quote(pid)}/artifacts/archive")
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    assert "filename*=UTF-8''" in cd          # RFC 5987 form carries the real name
    assert 'filename="artifacts-artifacts.zip"' in cd  # ASCII fallback (no ASCII-safe chars in pid)


def test_archive_quote_and_crlf_in_pid_yields_safe_header():
    # A pid containing '"'/CR/LF can't survive HTTP routing as a raw path
    # segment (TestClient/httpx reject or mangle CRLF in URLs before this
    # even reaches the route), so we unit-test the header builder directly
    # rather than going through client.get(...).
    from pathfinder.routes.artifacts import _content_disposition
    pid = 'we"ird\r\npid'
    cd = _content_disposition(pid)
    assert "\r" not in cd and "\n" not in cd
    fallback = cd.split('filename="')[1].split('"')[0]
    assert '"' not in fallback
