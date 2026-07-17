import asyncio
from fastapi.testclient import TestClient
from pathfinder.app import app, registry

client = TestClient(app)


def _seed(pid):
    client.post("/projects", json={"project_id": pid})
    ws = registry.get(pid)
    # asyncio.run (not get_event_loop().run_until_complete) — matches the
    # style already used in test_routes_answers.py / test_routes_turns.py.
    async def seed():
        await ws.sandbox.write_file("aiplc-docs/discovery-mode-selection-questions.md", "x")
        await ws.sandbox.write_file("aiplc-docs/discovery/product-strategy/strategy-questions.md", "y")
        await ws.sandbox.write_file("aiplc-docs/audit.md", "z")
        await ws.sandbox.write_file("aiplc-docs/discovery/discovery-document.md", "w")
    asyncio.run(seed())


def test_list_questions_route():
    _seed("disc-q1")
    r = client.get("/projects/disc-q1/questions")
    assert r.status_code == 200
    assert sorted(r.json()["questions"]) == [
        "aiplc-docs/discovery-mode-selection-questions.md",
        "aiplc-docs/discovery/product-strategy/strategy-questions.md",
    ]


def test_list_artifacts_route():
    _seed("disc-a1")
    r = client.get("/projects/disc-a1/artifacts")
    assert r.status_code == 200
    assert sorted(r.json()["artifacts"]) == [
        "aiplc-docs/audit.md",
        "aiplc-docs/discovery-mode-selection-questions.md",
        "aiplc-docs/discovery/discovery-document.md",
        "aiplc-docs/discovery/product-strategy/strategy-questions.md",
    ]


def test_list_questions_unknown_project_404():
    r = client.get("/projects/nope-disc/questions")
    assert r.status_code == 404


def test_list_artifacts_unknown_project_404():
    r = client.get("/projects/nope-disc2/artifacts")
    assert r.status_code == 404


def test_list_questions_route_does_not_collide_with_single_question_route():
    # /projects/{pid}/questions/{name:path} (routes/artifacts.py, Phase 1) must
    # keep working once the no-argument /projects/{pid}/questions route exists.
    _seed("disc-collide")
    r = client.get(
        "/projects/disc-collide/questions/aiplc-docs/discovery-mode-selection-questions.md"
    )
    assert r.status_code == 200
    assert r.json()["parse_ok"] is False  # seeded content "x" is not valid question markdown
