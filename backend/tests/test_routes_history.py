import json
from fastapi.testclient import TestClient
import pathfinder.app as app_module
from tests.fakes.in_memory_s3 import FakeS3Store

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

def test_history_returns_items_from_session_store(monkeypatch):
    _local_project(monkeypatch, "h1")
    s3 = FakeS3Store()
    s3.blobs["session_h1/agents/agent_default/messages/message_0.json"] = json.dumps(
        {"message": {"role": "user", "content": [{"text": "안녕"}]}, "message_id": 0})
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: s3)
    body = client.get("/projects/h1/history").json()
    assert body == {"items": [{"role": "user", "text": "안녕", "card": None, "name": None, "trace": []}]}

def test_history_empty_when_no_session(monkeypatch):
    _local_project(monkeypatch, "h2")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: FakeS3Store())
    assert client.get("/projects/h2/history").json() == {"items": []}

def test_history_unknown_project_404(monkeypatch):
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: FakeS3Store())
    assert client.get("/projects/ghost/history").status_code == 404

def test_history_degrades_when_factory_raises(monkeypatch):
    _local_project(monkeypatch, "h3")
    def boom():
        raise RuntimeError("aws profile broken")
    monkeypatch.setattr(app_module, "session_s3_factory", boom)
    r = client.get("/projects/h3/history")
    assert r.status_code == 200 and r.json() == {"items": []}
