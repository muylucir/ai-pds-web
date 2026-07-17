from fastapi.testclient import TestClient
from pathfinder.app import app

client = TestClient(app)


def test_create_project_without_name_returns_null_name():
    r = client.post("/projects", json={"project_id": "plist-noname"})
    assert r.status_code == 200
    assert r.json() == {"project_id": "plist-noname", "name": None}


def test_create_project_with_name_returns_it():
    r = client.post("/projects", json={"project_id": "plist-named", "name": "기획전 AI 어시스턴트"})
    assert r.status_code == 200
    assert r.json() == {"project_id": "plist-named", "name": "기획전 AI 어시스턴트"}


def test_list_projects_includes_created_projects_with_names():
    client.post("/projects", json={"project_id": "plist-a", "name": "Project A"})
    client.post("/projects", json={"project_id": "plist-b"})
    r = client.get("/projects")
    assert r.status_code == 200
    by_id = {p["project_id"]: p["name"] for p in r.json()["projects"]}
    assert by_id["plist-a"] == "Project A"
    assert by_id["plist-b"] is None


def test_list_projects_is_empty_capable():
    # Not asserting exact emptiness (other tests in the module-level registry
    # may have created projects already) — asserting the shape is always a list.
    r = client.get("/projects")
    assert r.status_code == 200
    assert isinstance(r.json()["projects"], list)
