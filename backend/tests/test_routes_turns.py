# backend/tests/test_routes_turns.py
import json
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
import pathfinder.app as app_module
from pathfinder.sandbox.local import LocalSandbox
from pathfinder.sandbox.base import AgentEvent

client = TestClient(app_module.app)

def _install_scripted(monkeypatch, pid, script):
    async def make(project_id):
        sb = LocalSandbox(root=Path(tempfile.mkdtemp()), script=script)
        await sb.start()
        return sb
    monkeypatch.setattr(app_module, "make_sandbox", make)
    client.post("/projects", json={"project_id": pid})

def _install_default(monkeypatch, pid):
    """Install a LocalSandbox with its default structured-demo script (no
    custom script override), so send_message arms a pending interrupt that
    send_answers/pending can then be exercised against."""
    async def make(project_id):
        sb = LocalSandbox(root=Path(tempfile.mkdtemp()))
        await sb.start()
        return sb
    monkeypatch.setattr(app_module, "make_sandbox", make)
    client.post("/projects", json={"project_id": pid})

def test_message_returns_events(monkeypatch):
    def script(text, sb):
        return [AgentEvent(kind="message", text=f"got {text}"), AgentEvent(kind="done")]
    _install_scripted(monkeypatch, "turn1", script)
    r = client.post("/projects/turn1/message", json={"text": "승인"})
    assert r.status_code == 200
    kinds = [e["kind"] for e in r.json()["events"]]
    assert kinds == ["message", "done"]
    assert "승인" in r.json()["events"][0]["text"]

def test_sse_stream_emits_frames(monkeypatch):
    def script(text, sb):
        return [AgentEvent(kind="status", text="working"),
                AgentEvent(kind="message", text="ok"),
                AgentEvent(kind="done")]
    _install_scripted(monkeypatch, "turn2", script)
    with client.stream("GET", "/projects/turn2/events", params={"text": "go"}) as r:
        body = "".join(chunk for chunk in r.iter_text())
    assert "working" in body
    assert "ok" in body
    assert '"kind":"done"' in body.replace(" ", "")

def test_message_redacts_credentials_in_event_text(monkeypatch):
    def script(text, sb):
        return [AgentEvent(kind="message", text="key AKIAIOSFODNN7EXAMPLE here"),
                AgentEvent(kind="done")]
    _install_scripted(monkeypatch, "turnred1", script)
    r = client.post("/projects/turnred1/message", json={"text": "go"})
    assert r.status_code == 200
    joined = " ".join(e.get("text") or "" for e in r.json()["events"])
    assert "AKIA" not in joined
    assert "[CREDENTIAL REDACTED]" in joined

def test_sse_redacts_credentials_in_event_text(monkeypatch):
    def script(text, sb):
        return [AgentEvent(kind="message", text="key AKIAIOSFODNN7EXAMPLE here"),
                AgentEvent(kind="done")]
    _install_scripted(monkeypatch, "turnred2", script)
    with client.stream("GET", "/projects/turnred2/events", params={"text": "go"}) as resp:
        body = "".join(chunk for chunk in resp.iter_text())
    assert "AKIA" not in body
    assert "[CREDENTIAL REDACTED]" in body

def test_answers_stream_relays_events(monkeypatch):
    _install_default(monkeypatch, "turnans1")
    # arm the pending interrupt via the default structured-demo script
    with client.stream("GET", "/projects/turnans1/events", params={"text": "시작"}) as r:
        list(r.iter_lines())
    answers = json.dumps({"1": "A", "2": "B"})
    with client.stream("GET", "/projects/turnans1/answers/stream",
                       params={"answers": answers}) as r:
        lines = [l for l in r.iter_lines() if l.startswith("data:")]
    kinds = [json.loads(l[len("data:"):].strip())["kind"] for l in lines]
    assert "document" in kinds and kinds[-1] == "done"

def test_pending_endpoint(monkeypatch):
    _install_default(monkeypatch, "turnpend1")
    assert client.get("/projects/turnpend1/pending").json() == {"pending": None}
    with client.stream("GET", "/projects/turnpend1/events", params={"text": "시작"}) as r:
        list(r.iter_lines())
    body = client.get("/projects/turnpend1/pending").json()
    assert body["pending"] is not None

def test_answers_stream_bad_json_400(monkeypatch):
    _install_default(monkeypatch, "turnbad1")
    r = client.get("/projects/turnbad1/answers/stream", params={"answers": "not-json"})
    assert r.status_code == 400

def test_answers_stream_unknown_project_404():
    r = client.get("/projects/does-not-exist/answers/stream", params={"answers": "{}"})
    assert r.status_code == 404

def test_pending_unknown_project_404():
    r = client.get("/projects/does-not-exist/pending")
    assert r.status_code == 404

def test_payload_is_redacted(monkeypatch):
    """questions payload with a credential-looking string is redacted at the
    route seam, same as text."""
    leak = json.dumps({"interrupt_id": "i", "questions": {
        "note": "key AKIAIOSFODNN7EXAMPLE here"}})
    def script(text, sb):
        return [AgentEvent(kind="questions", payload=leak), AgentEvent(kind="done")]
    _install_scripted(monkeypatch, "turnredpayload", script)
    with client.stream("GET", "/projects/turnredpayload/events", params={"text": "hi"}) as r:
        lines = [l for l in r.iter_lines() if l.startswith("data:")]
    body = "".join(lines)
    assert "AKIA" not in body
    assert "[CREDENTIAL REDACTED]" in body
