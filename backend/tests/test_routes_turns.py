# backend/tests/test_routes_turns.py
import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient
import aipds.app as app_module
from aipds.workspace import Workspace
from aipds.models import AgentEvent
from aipds.turn_marker import TURN_MARKER_KEY
from fakes.in_memory_s3 import FakeS3Store

client: TestClient


@pytest.fixture(autouse=True)
def _live_client():
    """A client that keeps the event loop alive between requests.

    A turn is server work, not a request (aipds/turn_job.py). A TestClient without `with`
    opens and closes a new loop per request, so a turn started by a POST disappears along
    with the loop before the next request; that does not happen in deployment.
    """
    global client
    with TestClient(app_module.app) as live:
        client = live
        yield

# Demo questions payload — a structured-demo shape so the answers/pending route
# tests can arm a pending interrupt with no AWS.
_DEMO_QUESTIONS = {
    "name": "pain-point-questions",
    "preamble": "데모 시나리오입니다.",
    "parse_ok": True,
    "raw_markdown": None,
    "questions": [
        {"number": 1, "category": "고객", "text": "주요 사용자는?", "answer": None,
         "options": [{"letter": "A", "text": "PM", "is_other": False, "recommended": True}]},
    ],
}


def _structured_first_turn(text):
    payload = json.dumps({"interrupt_id": "local-i-1", "questions": _DEMO_QUESTIONS},
                         ensure_ascii=False)
    return [
        AgentEvent(kind="message", text=f"'{text}' 요청을 받았습니다."),
        AgentEvent(kind="stage", payload=json.dumps(
            {"stage": "Envision", "status": "in_progress", "summary": "질문 생성"},
            ensure_ascii=False)),
        AgentEvent(kind="questions", payload=payload),
        AgentEvent(kind="done"),
    ]


class ScriptRunner:
    """A runner for route tests that need only send_message/send_answers/pending.

    Without a script it imitates the structured demo flow: send_message arms
    pending with a questions event, and send_answers emits document+done."""
    def __init__(self, script=None):
        self._script = script or _structured_first_turn
        self._pending_payload = None
        self.interrupts = 0

    async def send_message(self, text, record=None):
        for e in self._script(text):
            if e.kind == "questions":
                self._pending_payload = e.payload
            yield e

    async def send_answers(self, answers, record=None):
        if self._pending_payload is None:
            yield AgentEvent(kind="error", text="no pending questions")
            return
        self._pending_payload = None
        summary = ", ".join(f"{k}={v}" for k, v in sorted(answers.items()))
        for e in [
            AgentEvent(kind="message", text=f"답변({summary})을 반영했습니다."),
            AgentEvent(kind="stage", payload=json.dumps(
                {"stage": "Envision", "status": "completed", "summary": "답변 반영"},
                ensure_ascii=False)),
            AgentEvent(kind="document", payload=json.dumps(
                {"path": "aiplc-docs/discovery/discovery-document.md",
                 "version": "v1", "summary": "초안 생성"}, ensure_ascii=False)),
            AgentEvent(kind="done"),
        ]:
            yield e

    async def pending(self):
        return self._pending_payload

    async def interrupt(self):
        self.interrupts += 1

    async def stop(self):
        pass


def _watch(pid: str, turn_id: str, after: int = 0) -> list[str]:
    """All SSE lines of one turn; watches until the turn ends."""
    with client.stream("GET", f"/projects/{pid}/events",
                       params={"turn": turn_id, "after": after}) as r:
        assert r.status_code == 200, r.read()
        return list(r.iter_lines())


def _say(pid: str, text: str) -> list[str]:
    """Start a message turn and watch it to the end."""
    r = client.post(f"/projects/{pid}/turns", json={"text": text})
    assert r.status_code == 200, r.text
    return _watch(pid, r.json()["turn_id"])


def _answer(pid: str, answers: dict) -> list[str]:
    """Start an answer turn and watch it to the end."""
    r = client.post(f"/projects/{pid}/answers", json={"answers": answers})
    assert r.status_code == 200, r.text
    return _watch(pid, r.json()["turn_id"])


def _install_scripted(monkeypatch, pid, script):
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")  # offline: no durable manifest write

    async def make(project_id):
        return Workspace(ScriptRunner(script))

    monkeypatch.setattr(app_module, "make_workspace", make)
    client.post("/projects", json={"project_id": pid})


def _install_default(monkeypatch, pid):
    """Install a ScriptRunner with its default structured-demo script, so
    send_message arms a pending interrupt that send_answers/pending can then
    be exercised against.

    Returns the runner so a test can assert on what the route did to it
    (the interrupt route has no response body to check).
    """
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")
    runner = ScriptRunner()

    async def make(project_id):
        return Workspace(runner)

    monkeypatch.setattr(app_module, "make_workspace", make)
    client.post("/projects", json={"project_id": pid})
    return runner


def test_sse_stream_emits_frames(monkeypatch):
    def script(text):
        return [AgentEvent(kind="status", text="working"),
                AgentEvent(kind="message", text="ok"),
                AgentEvent(kind="done")]
    _install_scripted(monkeypatch, "turn2", script)
    body = "\n".join(_say("turn2", "go"))
    assert "working" in body
    assert "ok" in body
    assert '"kind":"done"' in body.replace(" ", "")

def test_sse_redacts_credentials_in_event_text(monkeypatch):
    def script(text):
        return [AgentEvent(kind="message", text="key AKIAIOSFODNN7EXAMPLE here"),
                AgentEvent(kind="done")]
    _install_scripted(monkeypatch, "turnred2", script)
    body = "\n".join(_say("turnred2", "go"))
    assert "AKIA" not in body
    assert "[CREDENTIAL REDACTED]" in body

def test_answers_stream_relays_events(monkeypatch):
    _install_default(monkeypatch, "turnans1")
    # arm the pending interrupt via the default structured-demo script
    _say("turnans1", "시작")
    lines = [l for l in _answer("turnans1", {"1": "A", "2": "B"})
             if l.startswith("data:")]
    kinds = [json.loads(l[len("data:"):].strip())["kind"] for l in lines]
    assert "document" in kinds and kinds[-1] == "done"

def test_pending_endpoint(monkeypatch):
    _install_default(monkeypatch, "turnpend1")
    assert client.get("/projects/turnpend1/pending").json() == {"pending": None}
    _say("turnpend1", "시작")
    body = client.get("/projects/turnpend1/pending").json()
    assert body["pending"] is not None

def test_pending_unknown_project_404():
    r = client.get("/projects/does-not-exist/pending")
    assert r.status_code == 404

def test_payload_is_redacted(monkeypatch):
    """questions payload with a credential-looking string is redacted at the
    route seam, same as text."""
    leak = json.dumps({"interrupt_id": "i", "questions": {
        "note": "key AKIAIOSFODNN7EXAMPLE here"}})
    def script(text):
        return [AgentEvent(kind="questions", payload=leak), AgentEvent(kind="done")]
    _install_scripted(monkeypatch, "turnredpayload", script)
    lines = [l for l in _say("turnredpayload", "hi") if l.startswith("data:")]
    body = "".join(lines)
    assert "AKIA" not in body
    assert "[CREDENTIAL REDACTED]" in body


# ---- Turn interruption ----

def test_interrupt_reaches_the_runner(monkeypatch):
    """Does the route actually reach the runner? A route that just returns 202 and does
    nothing is indistinguishable on screen: the button gets pressed and the turn keeps
    running."""
    runner = _install_default(monkeypatch, "int-1")
    r = client.post("/projects/int-1/interrupt")
    assert r.status_code == 202
    assert runner.interrupts == 1


def test_interrupt_is_idempotent(monkeypatch):
    """Pressing twice is the same. A user pressing again because nothing seemed to
    happen is a normal path, and it is not an error when no turn is running either."""
    runner = _install_default(monkeypatch, "int-2")
    assert client.post("/projects/int-2/interrupt").status_code == 202
    assert client.post("/projects/int-2/interrupt").status_code == 202
    assert runner.interrupts == 2


def test_interrupt_on_an_unknown_project_is_404(monkeypatch):
    """ensure_workspace's existing contract: a nonexistent project is 404. This is
    separate from interruption being idempotent (a missing turn in an existing project ≠
    a missing project)."""
    _install_default(monkeypatch, "int-3")
    assert client.post("/projects/nope/interrupt").status_code == 404


def test_interrupt_survives_a_raising_runner(monkeypatch):
    """Even if the driver's client.interrupt() raises, the 202/idempotent contract
    promised by the route's docstring holds; the user just presses again thinking the
    interrupt did not take effect, and has no reason to receive a 500."""
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")
    runner = ScriptRunner()

    async def boom():
        raise RuntimeError("subprocess pipe closed")
    runner.interrupt = boom

    async def make(project_id):
        return Workspace(runner)
    monkeypatch.setattr(app_module, "make_workspace", make)
    client.post("/projects", json={"project_id": "int-4"})

    assert client.post("/projects/int-4/interrupt").status_code == 202


# ---- Two-step handle that moves long input out of the URL (HTTP 431 defect) ----
#
# The observed defect: a 2,164-character Korean input became a 14,376-byte request line
# through encodeURIComponent, and together with the auth cookies (3 JWTs, ~3.7 KB) it
# exceeded Node's maxHeaderSize of 16,384 bytes, so the proxy returned 431. EventSource
# does not expose the status code, so the screen showed only "연결이 끊어졌습니다" (connection
# lost).

_LONG_KO = "가" * 3000


def test_turn_handle_carries_text_out_of_the_url(monkeypatch):
    """Accept the text via POST; SSE carries only a short handle in the URL."""
    seen = {}

    def script(text):
        seen["text"] = text
        return [AgentEvent(kind="message", text="ok"), AgentEvent(kind="done")]

    _install_scripted(monkeypatch, "turnh1", script)
    r = client.post("/projects/turnh1/turns", json={"text": _LONG_KO})
    assert r.status_code == 200
    handle = r.json()["turn_id"]
    # This is the core assertion of this design: the handle goes into the URL, so it
    # must be short.
    assert len(handle) <= 64
    with client.stream("GET", "/projects/turnh1/events",
                       params={"turn": handle}) as resp:
        body = "".join(chunk for chunk in resp.iter_text())
    assert "ok" in body
    # The agent received the full original text; the handle does not truncate it.
    assert seen["text"] == _LONG_KO


def _data(lines):
    return [json.loads(l[len("data:"):].strip()) for l in lines
            if l.startswith("data:")]


def test_a_turn_can_be_watched_again_from_any_seq(monkeypatch):
    """A turn id is not a one-time handle but a name for **re-watching**.

    A reload, a return from sleep, and a second tab all watch the same turn from the start
    (`after=0`) or from where they last received (`after=n`).
    """
    def script(text):
        return [AgentEvent(kind="message", text="하나"),
                AgentEvent(kind="message", text="둘"),
                AgentEvent(kind="done")]
    _install_scripted(monkeypatch, "turnh2", script)
    turn = client.post("/projects/turnh2/turns",
                       json={"text": "go"}).json()["turn_id"]
    with client.stream("GET", "/projects/turnh2/events",
                       params={"turn": turn}) as r:
        first = list(r.iter_lines())
    with client.stream("GET", "/projects/turnh2/events",
                       params={"turn": turn}) as r:
        again = list(r.iter_lines())
    assert _data(first) == _data(again)
    assert [e["text"] for e in _data(first)[:2]] == ["하나", "둘"]
    # The frame's id is the seq; it is passed back as `after` when reattaching.
    assert "id: 1" in first
    with client.stream("GET", "/projects/turnh2/events",
                       params={"turn": turn, "after": 1}) as r:
        tail = _data(list(r.iter_lines()))
    assert [e.get("text") for e in tail] == ["둘", None]
    assert tail[-1]["kind"] == "done"


def test_an_unknown_turn_is_404(monkeypatch):
    _install_default(monkeypatch, "turnh3")
    r = client.get("/projects/turnh3/events", params={"turn": "deadbeef"})
    assert r.status_code == 404


def test_events_only_watches_a_turn(monkeypatch):
    """`/events` only **watches** a turn; the POST starts it. There is no way to start one
    with input in the URL, so long input never ends up in the request line (431)."""
    _install_default(monkeypatch, "turnh4")
    assert client.get("/projects/turnh4/events").status_code == 422
    assert client.get("/projects/turnh4/events",
                      params={"text": "go"}).status_code == 422


def test_a_turn_is_scoped_to_its_project(monkeypatch):
    _install_default(monkeypatch, "turnh6")
    _install_default(monkeypatch, "turnh7")
    turn = client.post("/projects/turnh6/turns",
                       json={"text": "p6의 입력"}).json()["turn_id"]
    # It cannot be watched with the same id from a different project.
    r = client.get("/projects/turnh7/events", params={"turn": turn})
    assert r.status_code == 404


def test_turns_unknown_project_404():
    r = client.post("/projects/does-not-exist/turns", json={"text": "hi"})
    assert r.status_code == 404


def test_answers_handle_carries_answers_out_of_the_url(monkeypatch):
    """Answer submission uses the same plumbing; long free-form text would hit the same
    limit."""
    _install_default(monkeypatch, "turnh8")
    _say("turnh8", "시작")
    long_answers = {"1": "A", "2": "긴 자유 서술 " * 400}
    r = client.post("/projects/turnh8/answers", json={"answers": long_answers})
    assert r.status_code == 200
    handle = r.json()["turn_id"]
    assert len(handle) <= 64
    lines = [l for l in _watch("turnh8", handle) if l.startswith("data:")]
    kinds = [json.loads(l[len("data:"):].strip())["kind"] for l in lines]
    assert kinds[-1] == "done"


# ---- A turn is server work ----
#
# A turn's lifetime does not belong to the request (aipds/turn_job.py header). The tests
# below create "running" with a turn whose runner stops at `gate`.

class GatedRunner(ScriptRunner):
    """A turn that emits the first message and then stops until `gate` opens. Records
    whether it ran to the end."""

    def __init__(self):
        super().__init__()
        self.gate = None
        self.finished = False
        self.sent = []

    async def send_message(self, text, record=None):
        self.sent.append(text)
        self.gate = self.gate or asyncio.Event()
        try:
            yield AgentEvent(kind="message", text="첫 문장")
            await self.gate.wait()
            yield AgentEvent(kind="message", text="자리를 비운 동안 온 문장")
            yield AgentEvent(kind="done")
        finally:
            # Where the runner's final sync runs; only once it gets here do artifacts
            # reach S3.
            self.finished = True


def _install_gated(monkeypatch, pid) -> GatedRunner:
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")
    runner = GatedRunner()

    async def make(project_id):
        return Workspace(runner)

    monkeypatch.setattr(app_module, "make_workspace", make)
    client.post("/projects", json={"project_id": pid})
    return runner


def _turn_state(pid):
    return client.get(f"/projects/{pid}/turn").json()["turn"]


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached")


def _open_gate(runner, delay: float = 0.0):
    """Let the turn continue. If `delay` is given, open it after that long.

    TestClient holds even a stream request until the app finishes responding, so the
    side that opens the stream cannot open the gate; it is scheduled in advance. A
    watching screen leaving **midway** cannot be created here for the same reason, so
    tests/test_turn_job.py covers it.
    """
    client.portal.start_task_soon(_set, runner, delay)


async def _set(runner, delay):
    await asyncio.sleep(delay)
    runner.gate.set()


def test_a_second_turn_while_one_runs_is_409_with_the_running_id(monkeypatch):
    """What the rejected side should do is not resend but watch the running turn."""
    runner = _install_gated(monkeypatch, "own2")
    turn = client.post("/projects/own2/turns", json={"text": "go"}).json()["turn_id"]
    r = client.post("/projects/own2/turns", json={"text": "또"})
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "turn_in_progress", "turn_id": turn}
    assert runner.sent == ["go"]    # the second input did not reach the agent
    _open_gate(runner)
    _wait_for(lambda: _turn_state("own2")["state"] == "done")
    # After it finishes, input is accepted again.
    assert client.post("/projects/own2/turns", json={"text": "또"}).status_code == 200


def test_turn_reports_the_running_turn_then_its_end(monkeypatch):
    runner = _install_gated(monkeypatch, "own3")
    assert _turn_state("own3") is None
    turn = client.post("/projects/own3/turns", json={"text": "go"}).json()["turn_id"]
    _wait_for(lambda: _turn_state("own3")["last_seq"] >= 1)
    state = _turn_state("own3")
    assert state["turn_id"] == turn and state["state"] == "running"
    _open_gate(runner)
    _wait_for(lambda: _turn_state("own3")["state"] == "done")


def test_turn_reports_a_turn_the_restart_cut_off(monkeypatch):
    """If there is no turn in memory but the marker is `running`, the turn was cut off
    by a restart."""
    _install_default(monkeypatch, "own4")
    s3 = FakeS3Store()
    s3.blobs[TURN_MARKER_KEY] = json.dumps(
        {"turn_id": "t-before", "kind": "message", "state": "running"})
    monkeypatch.setenv("AIPDS_S3_BUCKET", "bucket")
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: s3)
    state = _turn_state("own4")
    assert state == {"turn_id": "t-before", "kind": "message",
                     "state": "interrupted", "last_seq": 0}


def test_a_finished_marker_is_not_reported_as_interrupted(monkeypatch):
    _install_default(monkeypatch, "own5")
    s3 = FakeS3Store()
    s3.blobs[TURN_MARKER_KEY] = json.dumps(
        {"turn_id": "t-before", "kind": "message", "state": "done"})
    monkeypatch.setenv("AIPDS_S3_BUCKET", "bucket")
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: s3)
    assert _turn_state("own5") is None


