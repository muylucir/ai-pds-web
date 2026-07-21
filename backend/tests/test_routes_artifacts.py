# backend/tests/test_routes_artifacts.py
import asyncio
from pathlib import Path
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
