# backend/tests/test_routes_artifacts.py
from pathlib import Path
from fastapi.testclient import TestClient
from pathfinder.app import app, registry

FIX = Path(__file__).parent / "fixtures"
client = TestClient(app)

def _create_and_seed(pid):
    assert client.post("/projects", json={"project_id": pid}).status_code == 200
    ws = registry.get(pid)
    import asyncio
    async def seed():
        await ws.sandbox.write_file("aiplc-docs/aiplc-state.md",
            (FIX / "aiplc-state.md").read_text(encoding="utf-8"))
        await ws.sandbox.write_file("aiplc-docs/strategy-questions.md",
            (FIX / "strategy-questions.md").read_text(encoding="utf-8"))
    asyncio.get_event_loop().run_until_complete(seed())

def test_create_project_conflict():
    client.post("/projects", json={"project_id": "dup"})
    r = client.post("/projects", json={"project_id": "dup"})
    assert r.status_code == 409

def test_get_state_route():
    _create_and_seed("proj-state")
    r = client.get("/projects/proj-state/state")
    assert r.status_code == 200
    assert r.json()["project_type"] == "Greenfield"

def test_get_questions_route():
    _create_and_seed("proj-q")
    r = client.get("/projects/proj-q/questions/aiplc-docs/strategy-questions.md")
    assert r.status_code == 200
    assert len(r.json()["questions"]) == 13

def test_unknown_project_404():
    assert client.get("/projects/nope/state").status_code == 404
