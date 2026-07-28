# backend/tests/test_routes_prototypes.py — prototype session/host/proxy routes.
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

import pathfinder.app as app_module
from pathfinder.models import AgentEvent
from pathfinder.proto.host import HostInfo
from pathfinder.proto.limits import BuildSemaphore
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
        # Recorded so a test can assert WHICH directory hosting was pointed at.
        # The real ProtoHost defaults to {root}/{pid}/{slug} when cwd is None,
        # but the build output lives one level down in prototype/ -- dropping
        # cwd here is what let that mismatch ship (npm ENOENT on package.json).
        self.start_cwds: list[object] = []

    async def start(self, pid, slug, cwd=None):
        self.start_cwds.append(cwd)
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
def proto_env(monkeypatch, tmp_path):
    """Registered project + fake S3/session/host wiring."""
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "")
    # VM env vars are gone -- the session route's config guard now checks a
    # build slot, not an image ARN.
    fake_s3 = FakeS3Store()

    async def fake_make_workspace(pid):
        return Workspace(FakeRunner(FakeS3Store()))

    monkeypatch.setattr(app_module, "make_workspace", fake_make_workspace)
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: fake_s3)
    # list_prototypes' "built" signal reads the local build dir straight off
    # disk (app_module._proto_root() / pid / slug) -- point it at tmp_path so
    # tests never touch the real ~/pathfinder-protos, matching
    # test_routes_prototypes_archive.py's fixture.
    monkeypatch.setattr(app_module, "_proto_root", lambda: tmp_path)

    fake_host = FakeProtoHost()
    monkeypatch.setattr(app_module, "proto_host", lambda: fake_host)
    monkeypatch.setattr(app_module, "build_semaphore",
                        BuildSemaphore(max_concurrent=2))

    sessions_backup = dict(app_module.proto_sessions)
    app_module.proto_sessions.clear()

    resp = client.post("/projects", json={"project_id": PID})
    assert resp.status_code in (200, 201, 409)

    yield {"s3": fake_s3, "host": fake_host, "root": tmp_path}

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
    assert body["prototypes"] == [{"slug": SLUG, "spec_path": SPEC_KEY,
                                   "state": "none", "port": None}]


def test_list_state_built(proto_env):
    _seed_spec(proto_env["s3"])
    proto_env["s3"].blobs[f"prototypes/{SLUG}/bundle/package.json"] = "{}"
    body = client.get(f"/projects/{PID}/prototypes").json()
    assert body["prototypes"][0]["state"] == "built"


def test_list_state_built_from_local_build_dir_with_nothing_in_s3(proto_env):
    """The regression this guards: the in-process builder writes straight into
    the LOCAL build dir (prototype/ subtree) and hosting serves it in place --
    nothing writes the S3 prototypes/{slug}/bundle/ prefix anymore (that was
    the deleted MicroVM's job). Against the old bundle-only check this card
    would incorrectly come back "none", hiding hosting/download for a
    perfectly finished prototype."""
    _seed_spec(proto_env["s3"])
    proto_dir = proto_env["root"] / PID / SLUG / "prototype"
    proto_dir.mkdir(parents=True)
    (proto_dir / "app.js").write_text("console.log(1)", encoding="utf-8")

    body = client.get(f"/projects/{PID}/prototypes").json()

    assert body["prototypes"][0]["state"] == "built"


def test_list_state_built_after_a_finished_build_session_goes_ready(proto_env, monkeypatch):
    """The regression this guards: a FINISHED build must read as "built", not
    "building".

    PrototypeSession sets status="ready" on the turn's `done` event -- "ready"
    means ready for ANOTHER turn (the session stays open so the user can ask
    for changes), not still working. But "ready" was in the set that made this
    route report "building", and that branch is an `if` ahead of the `built`
    check, so the card stayed 빌드 중 forever: nothing removes the session from
    proto_sessions on completion (only a retry or an explicit DELETE does), so
    a reload re-derived the same wrong answer. 빌드 완료/실행 were unreachable.
    """
    _seed_spec(proto_env["s3"])
    session = FakePrototypeSession()
    _install_session_factory(monkeypatch, session)
    client.post(f"/projects/{PID}/prototypes/{SLUG}/session")
    # The agent finished and wrote its output; the session is idle-but-open.
    session.status = "ready"
    proto_dir = proto_env["root"] / PID / SLUG / "prototype"
    proto_dir.mkdir(parents=True)
    (proto_dir / "package.json").write_text("{}", encoding="utf-8")

    body = client.get(f"/projects/{PID}/prototypes").json()

    assert body["prototypes"][0]["state"] == "built"


def test_list_state_building_only_while_the_session_is_actually_working(proto_env, monkeypatch):
    """Complement to the test above: the statuses that DO mean work in flight
    must still report "building" even once output exists on disk -- otherwise
    the fix would flip the card to 빌드 완료 mid-build and let the user start
    hosting against a tree the agent is still writing into (which
    start_host's own 409 guard then has to catch)."""
    _seed_spec(proto_env["s3"])
    session = FakePrototypeSession()
    _install_session_factory(monkeypatch, session)
    client.post(f"/projects/{PID}/prototypes/{SLUG}/session")
    proto_dir = proto_env["root"] / PID / SLUG / "prototype"
    proto_dir.mkdir(parents=True)
    (proto_dir / "package.json").write_text("{}", encoding="utf-8")

    for status in ("starting", "building", "waiting_input"):
        session.status = status
        body = client.get(f"/projects/{PID}/prototypes").json()
        assert body["prototypes"][0]["state"] == "building", status


def test_a_ready_session_still_blocks_a_second_start_and_serves_its_stream(proto_env, monkeypatch):
    """Guards the coupling the fix must NOT break. `_LIVE_STATUSES` fed both
    this route's display state and -- via a separate `_DEAD_STATUSES` check --
    the 409/404 liveness questions. A "ready" session is still a LIVE session:
    POST must refuse a second one (409) and the events stream must serve it,
    even though the card now reads "built".

    The comment at _DEAD_STATUSES records why this matters: taking a status out
    of the wrong set once wedged prototypes permanently -- POST said "already
    active" while GET said "no active session", so the user could neither
    restart nor stream."""
    _seed_spec(proto_env["s3"])
    session = FakePrototypeSession()
    _install_session_factory(monkeypatch, session)
    client.post(f"/projects/{PID}/prototypes/{SLUG}/session")
    session.status = "ready"

    assert client.post(
        f"/projects/{PID}/prototypes/{SLUG}/session").status_code == 409
    # A live session must still be streamable (404 would mean "no session").
    with client.stream(
            "GET",
            f"/projects/{PID}/prototypes/{SLUG}/events?text=hi") as resp:
        assert resp.status_code == 200


def test_list_state_not_built_when_only_the_spec_file_exists(proto_env):
    """PrototypeSession.start() seeds the build dir with the spec .md file
    before the agent does anything -- a directory containing only that (the
    mirror-image bug) means a session merely STARTED, not that anything was
    BUILT. Must not be reported as built."""
    _seed_spec(proto_env["s3"])
    spec_dir = (proto_env["root"] / PID / SLUG /
               "aiplc-docs" / "discovery" / "prototypes" / SLUG)
    spec_dir.mkdir(parents=True)
    (spec_dir / f"PROTOTYPE-{SLUG}.md").write_text("# spec", encoding="utf-8")

    body = client.get(f"/projects/{PID}/prototypes").json()

    assert body["prototypes"][0]["state"] == "none"


def test_list_state_building_wins_over_built(proto_env, monkeypatch):
    """A live session still wins as building, even with real build output
    already on disk from a prior successful run (e.g. a rebuild in
    progress)."""
    _seed_spec(proto_env["s3"])
    proto_dir = proto_env["root"] / PID / SLUG / "prototype"
    proto_dir.mkdir(parents=True)
    (proto_dir / "app.js").write_text("console.log(1)", encoding="utf-8")
    session = FakePrototypeSession()
    session.status = "building"
    app_module.proto_sessions[(PID, SLUG)] = session

    body = client.get(f"/projects/{PID}/prototypes").json()

    assert body["prototypes"][0]["state"] == "building"


def test_list_state_running_wins_over_built(proto_env):
    """A running host still wins as running over a merely-built prototype."""
    _seed_spec(proto_env["s3"])
    proto_dir = proto_env["root"] / PID / SLUG / "prototype"
    proto_dir.mkdir(parents=True)
    (proto_dir / "app.js").write_text("console.log(1)", encoding="utf-8")
    proto_env["host"].infos[(PID, SLUG)] = HostInfo(state="running", port=4007,
                                                    log_tail="")

    body = client.get(f"/projects/{PID}/prototypes").json()

    assert body["prototypes"][0]["state"] == "running"
    assert body["prototypes"][0]["port"] == 4007


def test_list_state_building(proto_env, monkeypatch):
    _seed_spec(proto_env["s3"])
    session = FakePrototypeSession()
    session.status = "building"
    app_module.proto_sessions[(PID, SLUG)] = session
    body = client.get(f"/projects/{PID}/prototypes").json()
    assert body["prototypes"][0]["state"] == "building"


def test_list_state_running_with_port(proto_env):
    _seed_spec(proto_env["s3"])
    proto_env["host"].infos[(PID, SLUG)] = HostInfo(state="running", port=4007,
                                                    log_tail="")
    body = client.get(f"/projects/{PID}/prototypes").json()
    assert body["prototypes"][0]["state"] == "running"
    assert body["prototypes"][0]["port"] == 4007


def test_list_reports_build_capacity(proto_env):
    _seed_spec(proto_env["s3"])
    body = client.get(f"/projects/{PID}/prototypes").json()
    assert body["active_builds"] == 0
    assert body["max_builds"] == 2
    assert [p["slug"] for p in body["prototypes"]] == [SLUG]


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


def test_session_start_429_when_the_cap_is_reached(proto_env, monkeypatch):
    """Third concurrent build is refused, not queued -- and the message has to
    say why, since a bare 429 reads as a bug to a workshop attendee."""
    monkeypatch.setattr(app_module, "build_semaphore",
                        BuildSemaphore(max_concurrent=1))
    _seed_spec(proto_env["s3"])
    _install_session_factory(monkeypatch, FakePrototypeSession())

    first = client.post(f"/projects/{PID}/prototypes/{SLUG}/session")
    assert first.status_code == 202

    proto_env["s3"].blobs[
        "aiplc-docs/discovery/prototypes/other/PROTOTYPE-other.md"] = "# other"
    second = client.post(f"/projects/{PID}/prototypes/other/session")
    assert second.status_code == 429
    assert "빌드" in second.json()["detail"]


def test_session_start_releases_the_slot_when_start_fails(proto_env, monkeypatch):
    """A failed start must not burn a slot permanently -- otherwise two bad
    attempts wedge the whole backend at cap 2."""
    sem = BuildSemaphore(max_concurrent=1)
    monkeypatch.setattr(app_module, "build_semaphore", sem)
    _seed_spec(proto_env["s3"])
    _install_session_factory(
        monkeypatch, FakePrototypeSession(start_exc=RuntimeError("boom")))

    assert client.post(f"/projects/{PID}/prototypes/{SLUG}/session").status_code == 502
    assert sem.snapshot()["active_builds"] == 0


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


def test_host_start_targets_the_prototype_subtree_not_the_build_dir(proto_env):
    """The regression this guards: hosting must be pointed at the SAME
    directory `_local_build_exists` calls built -- {root}/{pid}/{slug}/prototype
    -- not the build dir above it.

    The build dir holds the spec .md that PrototypeSession.start() seeds (plus
    .proto-host.log/.pid from a prior attempt), so ProtoHost's own
    `target_dir.is_dir()` guard passes there and the failure surfaces later as
    `npm error ENOENT ... /{slug}/package.json` -> state=failed -> HTTP 502.
    A 404 ("not built") would at least have been honest; the card meanwhile
    showed 실행 because the list check looked in the right place."""
    _seed_spec(proto_env["s3"])
    proto_dir = proto_env["root"] / PID / SLUG / "prototype"
    proto_dir.mkdir(parents=True)
    (proto_dir / "package.json").write_text("{}", encoding="utf-8")

    assert client.post(f"/projects/{PID}/prototypes/{SLUG}/host").status_code == 200

    assert proto_env["host"].start_cwds == [proto_dir]


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


def test_host_start_409_while_a_build_session_is_live(proto_env, monkeypatch):
    """Hosting used to wipe and re-download the directory; now it serves the
    build directory in place, so starting it under a live build must be
    refused rather than racing the agent."""
    _seed_spec(proto_env["s3"])
    session = FakePrototypeSession()
    _install_session_factory(monkeypatch, session)
    client.post(f"/projects/{PID}/prototypes/{SLUG}/session")

    resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/host")
    assert resp.status_code == 409


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


def test_proxy_root_redirects_relatively_to_add_trailing_slash(
        proto_env, echo_server):
    """`/proto/{pid}/{slug}` must redirect to the slash form with a RELATIVE
    Location. Starlette's default is an ABSOLUTE 307 naming this server's own
    origin, which sends a browser on the public host to localhost:8000 (hang).
    The slash also matters for content: prototype HTML uses relative asset
    refs, which at the slash-less URL resolve one level too high (slug lost)."""
    proto_env["host"].infos[(PID, SLUG)] = HostInfo(
        state="running", port=echo_server, log_tail="")
    resp = client.get(f"/proto/{PID}/{SLUG}", follow_redirects=False)
    assert resp.status_code == 307
    location = resp.headers["location"]
    assert location == f"/proto/{PID}/{SLUG}/"
    assert "://" not in location  # never absolute — must stay on the public host


def test_proxy_root_redirect_preserves_query(proto_env, echo_server):
    proto_env["host"].infos[(PID, SLUG)] = HostInfo(
        state="running", port=echo_server, log_tail="")
    resp = client.get(f"/proto/{PID}/{SLUG}?a=1&b=2", follow_redirects=False)
    assert resp.headers["location"] == f"/proto/{PID}/{SLUG}/?a=1&b=2"


def test_proxy_relative_asset_under_slug_prefix_is_served(proto_env, echo_server):
    """The asset path a browser derives from the slash form
    (.../{slug}/styles.css) must reach the prototype — these were the 502s."""
    proto_env["host"].infos[(PID, SLUG)] = HostInfo(
        state="running", port=echo_server, log_tail="")
    resp = client.get(f"/proto/{PID}/{SLUG}/styles.css")
    assert resp.status_code == 200
    assert resp.text == "echo:/styles.css"


def test_proxy_rewrites_upstream_absolute_redirect(proto_env):
    """A prototype redirecting to its own internal origin must be rewritten to
    the public proxy path — otherwise the browser chases 127.0.0.1:<port>."""
    from pathfinder.routes.proto_public import _rewritten_location
    got = _rewritten_location(
        "http://127.0.0.1:4001/login?next=/dash", PID, SLUG)
    assert got == f"/proto/{PID}/{SLUG}/login?next=/dash"


def test_proxy_rewrites_upstream_relative_root_redirect(proto_env):
    from pathfinder.routes.proto_public import _rewritten_location
    assert _rewritten_location("/dashboard", PID, SLUG) == \
        f"/proto/{PID}/{SLUG}/dashboard"


def test_proxy_leaves_external_redirect_alone(proto_env):
    from pathfinder.routes.proto_public import _rewritten_location
    ext = "https://accounts.google.com/o/oauth2/auth?x=1"
    assert _rewritten_location(ext, PID, SLUG) == ext
