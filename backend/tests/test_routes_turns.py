# backend/tests/test_routes_turns.py
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
import pathfinder.app as app_module
from pathfinder.sandbox.local import LocalSandbox
from pathfinder.sandbox.base import AgentEvent

client = TestClient(app_module.app)

def _install_scripted(pid, script):
    async def make(project_id):
        sb = LocalSandbox(root=Path(tempfile.mkdtemp()), script=script)
        await sb.start()
        return sb
    app_module.make_sandbox = make
    client.post("/projects", json={"project_id": pid})

def test_message_returns_events():
    def script(text, sb):
        return [AgentEvent(kind="message", text=f"got {text}"), AgentEvent(kind="done")]
    _install_scripted("turn1", script)
    r = client.post("/projects/turn1/message", json={"text": "승인"})
    assert r.status_code == 200
    kinds = [e["kind"] for e in r.json()["events"]]
    assert kinds == ["message", "done"]
    assert "승인" in r.json()["events"][0]["text"]

def test_sse_stream_emits_frames():
    def script(text, sb):
        return [AgentEvent(kind="status", text="working"),
                AgentEvent(kind="message", text="ok"),
                AgentEvent(kind="done")]
    _install_scripted("turn2", script)
    with client.stream("GET", "/projects/turn2/events", params={"text": "go"}) as r:
        body = "".join(chunk for chunk in r.iter_text())
    assert "working" in body
    assert "ok" in body
    assert '"kind":"done"' in body.replace(" ", "")
