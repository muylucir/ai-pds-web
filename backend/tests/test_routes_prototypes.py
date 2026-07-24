# backend/tests/test_routes_prototypes.py — prototype session/host/proxy routes.
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

import pathfinder.app as app_module
from pathfinder.models import AgentEvent
from pathfinder.proto.host import HostInfo
from pathfinder.workspace import Workspace
from fakes.fake_runner import FakeRunner
from fakes.in_memory_s3 import FakeS3Store

client = TestClient(app_module.app)

PID = "proto-route-test"
SLUG = "demo"
SPEC_KEY = f"aiplc-docs/discovery/prototypes/{SLUG}/PROTOTYPE-{SLUG}.md"


class FakePrototypeSession:
    """Scripted PrototypeSession stand-in: records calls, plays back events."""

    def __init__(self, events=None, start_exc=None):
        self.status = "starting"
        self.started = False
        self.closed = False
        self.interrupts = 0
        self.messages: list[str] = []
        self.answers_calls: list[dict] = []
        self.answers_result = True
        self._events = events or [AgentEvent(kind="message", text="building"),
                                  AgentEvent(kind="done")]
        self._start_exc = start_exc

    def first_prompt(self) -> str:
        return "FIRST_PROMPT_TEXT"

    async def start(self):
        if self._start_exc is not None:
            raise self._start_exc
        self.started = True
        self.status = "ready"

    async def send_message(self, text):
        self.messages.append(text)
        self.status = "building"
        for ev in self._events:
            yield ev
        self.status = "ready"

    async def send_answers(self, answers):
        self.answers_calls.append(answers)
        return self.answers_result

    async def interrupt(self):
        self.interrupts += 1

    async def close(self):
        self.closed = True
        self.status = "closed"


class FakeProtoHost:
    def __init__(self):
        self.infos: dict[tuple[str, str], HostInfo] = {}
        self.start_exc = None
        self.stopped: list[tuple[str, str]] = []

    async def start(self, pid, slug):
        if self.start_exc is not None:
            raise self.start_exc
        info = self.infos.get((pid, slug))
        if info is None:
            info = HostInfo(state="running", port=4001, log_tail="started")
            self.infos[(pid, slug)] = info
        return info

    async def stop(self, pid, slug):
        self.stopped.append((pid, slug))
        self.infos.pop((pid, slug), None)

    def status(self, pid, slug):
        return self.infos.get((pid, slug))

    def log_tail(self, pid, slug, lines=100):
        info = self.infos.get((pid, slug))
        return info.log_tail if info else ""


@pytest.fixture()
def proto_env(monkeypatch):
    """Registered project + fake S3/session/host wiring."""
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "")
    # The session route refuses to start when no VM image is configured (a
    # real deploy footgun that used to surface as an instant 502) — these
    # tests inject a fake session factory, so satisfy the config guard.
    monkeypatch.setenv("PATHFINDER_VM_IMAGE_ID",
                       "arn:aws:lambda:ap-northeast-1:1:microvm-image:fake")
    monkeypatch.setenv("PATHFINDER_VM_ROLE_ARN", "arn:aws:iam::1:role/fake")
    fake_s3 = FakeS3Store()

    async def fake_make_workspace(pid):
        return Workspace(FakeRunner(FakeS3Store()))

    monkeypatch.setattr(app_module, "make_workspace", fake_make_workspace)
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: fake_s3)

    fake_host = FakeProtoHost()
    monkeypatch.setattr(app_module, "proto_host", lambda: fake_host)

    sessions_backup = dict(app_module.proto_sessions)
    app_module.proto_sessions.clear()

    resp = client.post("/projects", json={"project_id": PID})
    assert resp.status_code in (200, 201, 409)

    yield {"s3": fake_s3, "host": fake_host}

    app_module.proto_sessions.clear()
    app_module.proto_sessions.update(sessions_backup)
    app_module.registry.remove(PID)


def _install_session_factory(monkeypatch, session):
    monkeypatch.setattr(app_module, "proto_session_factory",
                        lambda pid, slug: session)


def _seed_spec(s3):
    s3.blobs[SPEC_KEY] = "# PROTOTYPE demo"


def _sse_events(resp_text):
    return [json.loads(line[len("data:"):].strip())
            for line in resp_text.splitlines() if line.startswith("data:")]


# ---- listing ----

def test_list_unknown_project_404(proto_env):
    assert client.get("/projects/nope/prototypes").status_code == 404


def test_list_state_none(proto_env):
    _seed_spec(proto_env["s3"])
    body = client.get(f"/projects/{PID}/prototypes").json()
    assert body == [{"slug": SLUG, "spec_path": SPEC_KEY,
                     "state": "none", "port": None}]


def test_list_state_built(proto_env):
    _seed_spec(proto_env["s3"])
    proto_env["s3"].blobs[f"prototypes/{SLUG}/bundle/package.json"] = "{}"
    body = client.get(f"/projects/{PID}/prototypes").json()
    assert body[0]["state"] == "built"


def test_list_state_building(proto_env, monkeypatch):
    _seed_spec(proto_env["s3"])
    session = FakePrototypeSession()
    session.status = "building"
    app_module.proto_sessions[(PID, SLUG)] = session
    body = client.get(f"/projects/{PID}/prototypes").json()
    assert body[0]["state"] == "building"


def test_list_state_running_with_port(proto_env):
    _seed_spec(proto_env["s3"])
    proto_env["host"].infos[(PID, SLUG)] = HostInfo(state="running", port=4007,
                                                    log_tail="")
    body = client.get(f"/projects/{PID}/prototypes").json()
    assert body[0]["state"] == "running"
    assert body[0]["port"] == 4007


# ---- session lifecycle ----

def test_session_start_202(proto_env, monkeypatch):
    session = FakePrototypeSession()
    _install_session_factory(monkeypatch, session)
    resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/session")
    assert resp.status_code == 202
    assert session.started
    assert app_module.proto_sessions[(PID, SLUG)] is session


def test_session_start_conflict_409(proto_env, monkeypatch):
    live = FakePrototypeSession()
    live.status = "ready"
    app_module.proto_sessions[(PID, SLUG)] = live
    _install_session_factory(monkeypatch, FakePrototypeSession())
    resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/session")
    assert resp.status_code == 409


def test_session_start_missing_spec_404(proto_env, monkeypatch):
    _install_session_factory(
        monkeypatch, FakePrototypeSession(start_exc=FileNotFoundError(SPEC_KEY)))
    resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/session")
    assert resp.status_code == 404


def test_session_start_boot_failure_502_sanitized(proto_env, monkeypatch):
    _install_session_factory(
        monkeypatch,
        FakePrototypeSession(start_exc=RuntimeError("AKIA-secret boom")))
    resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/session")
    assert resp.status_code == 502
    assert "AKIA" not in resp.text
    assert resp.json()["detail"] == "session start failed"


def test_events_no_session_404(proto_env):
    resp = client.get(f"/projects/{PID}/prototypes/{SLUG}/events",
                      params={"text": "hi"})
    assert resp.status_code == 404


def test_events_streams_and_redacts(proto_env):
    session = FakePrototypeSession(events=[
        AgentEvent(kind="message",
                   text="key=AKIAIOSFODNN7EXAMPLE1 done"),
        AgentEvent(kind="done"),
    ])
    session.status = "ready"
    app_module.proto_sessions[(PID, SLUG)] = session
    with client.stream("GET", f"/projects/{PID}/prototypes/{SLUG}/events",
                       params={"text": "build please"}) as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())
    events = _sse_events(text)
    assert [e["kind"] for e in events] == ["message", "done"]
    assert "AKIAIOSFODNN7EXAMPLE1" not in text
    assert session.messages == ["build please"]


def test_events_first_sentinel_uses_first_prompt(proto_env):
    session = FakePrototypeSession()
    session.status = "ready"
    app_module.proto_sessions[(PID, SLUG)] = session
    with client.stream("GET", f"/projects/{PID}/prototypes/{SLUG}/events",
                       params={"text": "__first__"}) as resp:
        "".join(resp.iter_text())
    assert session.messages == ["FIRST_PROMPT_TEXT"]


def test_answers_204_and_409(proto_env):
    session = FakePrototypeSession()
    session.status = "waiting_input"
    app_module.proto_sessions[(PID, SLUG)] = session
    resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/answers",
                       json={"answers": {"1": "A"}})
    assert resp.status_code == 204
    assert session.answers_calls == [{"1": "A"}]

    session.answers_result = False
    resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/answers",
                       json={"answers": {"1": "A"}})
    assert resp.status_code == 409


def test_answers_bad_body_422(proto_env):
    session = FakePrototypeSession()
    session.status = "waiting_input"
    app_module.proto_sessions[(PID, SLUG)] = session
    resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/answers",
                       json={"nope": 1})
    assert resp.status_code == 422


def test_interrupt_202_and_404(proto_env):
    assert client.post(
        f"/projects/{PID}/prototypes/{SLUG}/interrupt").status_code == 404
    session = FakePrototypeSession()
    session.status = "building"
    app_module.proto_sessions[(PID, SLUG)] = session
    resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/interrupt")
    assert resp.status_code == 202
    assert session.interrupts == 1


def test_close_session_204_removes_registry(proto_env):
    session = FakePrototypeSession()
    session.status = "ready"
    app_module.proto_sessions[(PID, SLUG)] = session
    resp = client.delete(f"/projects/{PID}/prototypes/{SLUG}/session")
    assert resp.status_code == 204
    assert session.closed
    assert (PID, SLUG) not in app_module.proto_sessions


def test_close_session_404_when_absent(proto_env):
    assert client.delete(
        f"/projects/{PID}/prototypes/{SLUG}/session").status_code == 404


# ---- hosting ----

def test_host_start_ok(proto_env):
    resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/host")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "running" and body["port"] == 4001


def test_host_start_no_bundle_404(proto_env):
    proto_env["host"].start_exc = FileNotFoundError("bundle")
    assert client.post(
        f"/projects/{PID}/prototypes/{SLUG}/host").status_code == 404


def test_host_start_failed_502_with_log(proto_env):
    proto_env["host"].infos[(PID, SLUG)] = HostInfo(
        state="failed", port=None, log_tail="npm ERR! broken")
    resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/host")
    assert resp.status_code == 502
    assert "npm ERR!" in resp.json()["detail"]


def test_host_status_and_stop(proto_env):
    assert client.get(
        f"/projects/{PID}/prototypes/{SLUG}/host").status_code == 404
    proto_env["host"].infos[(PID, SLUG)] = HostInfo(state="running", port=4003,
                                                    log_tail="ok")
    body = client.get(f"/projects/{PID}/prototypes/{SLUG}/host").json()
    assert body["state"] == "running" and body["port"] == 4003
    assert client.delete(
        f"/projects/{PID}/prototypes/{SLUG}/host").status_code == 204
    assert (PID, SLUG) in proto_env["host"].stopped


# ---- reverse proxy ----

class _EchoHandler(BaseHTTPRequestHandler):
    seen_headers: list[dict] = []

    def do_GET(self):
        type(self).seen_headers.append(dict(self.headers))
        body = f"echo:{self.path}".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: D102 — silence test server logging
        pass


@pytest.fixture()
def echo_server():
    _EchoHandler.seen_headers = []
    server = HTTPServer(("127.0.0.1", 0), _EchoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port
    server.shutdown()
    thread.join(timeout=5)


def test_proxy_streams_upstream_response(proto_env, echo_server):
    proto_env["host"].infos[(PID, SLUG)] = HostInfo(
        state="running", port=echo_server, log_tail="")
    resp = client.get(f"/proto/{PID}/{SLUG}/some/page",
                      params={"q": "1"},
                      headers={"X-Origin-Verify": "secret-value"})
    assert resp.status_code == 200
    assert resp.text == "echo:/some/page?q=1"
    # The CloudFront shared secret must not reach the prototype process.
    forwarded = _EchoHandler.seen_headers[0]
    assert not any(k.lower() == "x-origin-verify" for k in forwarded)


def test_proxy_502_when_not_running(proto_env):
    resp = client.get(f"/proto/{PID}/{SLUG}/index.html")
    assert resp.status_code == 502
    assert "start hosting first" in resp.text


def test_session_start_503_when_vm_image_unset(proto_env, monkeypatch):
    """Missing VM config must say so plainly (503), not fail deep inside boto3
    and surface as an opaque 502 the instant the user clicks 빌드 시작 —
    the exact symptom seen on the deployed EC2, whose systemd unit didn't
    carry PATHFINDER_VM_IMAGE_ID."""
    monkeypatch.delenv("PATHFINDER_VM_IMAGE_ID", raising=False)
    resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/session")
    assert resp.status_code == 503
    assert "PATHFINDER_VM_IMAGE_ID" in resp.json()["detail"]


def test_failed_session_does_not_wedge_prototype(proto_env, monkeypatch):
    """A failed session must neither block a restart (409) nor be served as a
    live stream (404): that combination wedged the prototype permanently —
    POST said 'already active' while GET said 'no active session'."""
    dead = FakePrototypeSession()
    dead.status = "failed"
    app_module.proto_sessions[(PID, SLUG)] = dead

    # A live stream must not be served off a dead session.
    assert client.get(f"/projects/{PID}/prototypes/{SLUG}/events",
                      params={"text": "hi"}).status_code == 404

    # ...and a restart must be allowed, replacing the corpse.
    fresh = FakePrototypeSession()
    _install_session_factory(monkeypatch, fresh)
    resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/session")
    assert resp.status_code == 202
    assert app_module.proto_sessions[(PID, SLUG)] is fresh
    assert fresh.started


def test_closed_session_also_allows_restart(proto_env, monkeypatch):
    """Same eviction path for a cleanly closed session."""
    done = FakePrototypeSession()
    done.status = "closed"
    app_module.proto_sessions[(PID, SLUG)] = done
    fresh = FakePrototypeSession()
    _install_session_factory(monkeypatch, fresh)
    assert client.post(
        f"/projects/{PID}/prototypes/{SLUG}/session").status_code == 202
    assert app_module.proto_sessions[(PID, SLUG)] is fresh


def test_session_start_503_when_role_arn_malformed(proto_env, monkeypatch):
    """A truncated/mangled ARN (e.g. a lost 'arn:' prefix from a hand-edited
    .env) must name the offending variable, not surface as an opaque 502 from
    deep inside botocore's ValidationException."""
    monkeypatch.setenv("PATHFINDER_VM_ROLE_ARN",
                       "aws:iam::939105814298:role/SomeRole")  # missing 'arn:'
    resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/session")
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "PATHFINDER_VM_ROLE_ARN" in detail and "valid ARN" in detail


def test_session_start_503_when_role_arn_unset(proto_env, monkeypatch):
    monkeypatch.delenv("PATHFINDER_VM_ROLE_ARN", raising=False)
    resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/session")
    assert resp.status_code == 503
    assert "PATHFINDER_VM_ROLE_ARN" in resp.json()["detail"]


def test_proxy_root_without_trailing_slash_is_served_not_redirected(
        proto_env, echo_server):
    """`/proto/{pid}/{slug}` (no trailing slash) must be proxied directly.
    Without a route for that shape Starlette answers with an ABSOLUTE 307 to
    its own origin, so a browser on the public host gets sent to
    localhost:8000 and hangs."""
    proto_env["host"].infos[(PID, SLUG)] = HostInfo(
        state="running", port=echo_server, log_tail="")
    resp = client.get(f"/proto/{PID}/{SLUG}", follow_redirects=False)
    assert resp.status_code == 200
    assert resp.text == "echo:/"


def test_proxy_rewrites_upstream_absolute_redirect(proto_env):
    """A prototype redirecting to its own internal origin must be rewritten to
    the public proxy path — otherwise the browser chases 127.0.0.1:<port>."""
    from pathfinder.routes.prototypes import _rewritten_location
    got = _rewritten_location(
        "http://127.0.0.1:4001/login?next=/dash", PID, SLUG)
    assert got == f"/proto/{PID}/{SLUG}/login?next=/dash"


def test_proxy_rewrites_upstream_relative_root_redirect(proto_env):
    from pathfinder.routes.prototypes import _rewritten_location
    assert _rewritten_location("/dashboard", PID, SLUG) == \
        f"/proto/{PID}/{SLUG}/dashboard"


def test_proxy_leaves_external_redirect_alone(proto_env):
    from pathfinder.routes.prototypes import _rewritten_location
    ext = "https://accounts.google.com/o/oauth2/auth?x=1"
    assert _rewritten_location(ext, PID, SLUG) == ext
