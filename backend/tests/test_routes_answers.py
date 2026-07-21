# backend/tests/test_routes_answers.py
from pathlib import Path
import asyncio
from fastapi.testclient import TestClient
import pathfinder.app as app_module
from pathfinder.app import app, registry
from pathfinder.workspace import Workspace
from fakes.fake_runner import FakeRunner

FIX = Path(__file__).parent / "fixtures"
client = TestClient(app)

def _seed(monkeypatch, pid):
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "")  # offline: no durable manifest write
    async def make(project_id):
        return Workspace(FakeRunner())
    monkeypatch.setattr(app_module, "make_workspace", make)
    client.post("/projects", json={"project_id": pid})
    ws = registry.get(pid)
    # Use asyncio.run (not get_event_loop().run_until_complete) — the latter is
    # deprecated on 3.11 and conflicts with pytest-asyncio's managed loop.
    asyncio.run(
        ws.runner.write_file("aiplc-docs/strategy-questions.md",
            (FIX / "strategy-questions.md").read_text(encoding="utf-8")))

def test_put_answers_updates_file(monkeypatch):
    _seed(monkeypatch, "ans1")
    r = client.put("/projects/ans1/questions/aiplc-docs/strategy-questions.md",
                   json={"answers": {"1": "B", "12": "A,C"}})
    assert r.status_code == 200
    by_num = {q["number"]: q["answer"] for q in r.json()["questions"]}
    assert by_num[1] == "B"
    assert by_num[12] == "A,C"

def test_put_unknown_question_400(monkeypatch):
    _seed(monkeypatch, "ans2")
    r = client.put("/projects/ans2/questions/aiplc-docs/strategy-questions.md",
                   json={"answers": {"99": "A"}})
    assert r.status_code == 400

def test_put_non_numeric_key_400(monkeypatch):
    _seed(monkeypatch, "ans3")
    r = client.put("/projects/ans3/questions/aiplc-docs/strategy-questions.md",
                   json={"answers": {"abc": "A"}})
    assert r.status_code == 400
