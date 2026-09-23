# backend/tests/test_routes_prototypes.py — prototype session/host/proxy routes.
import asyncio
import json
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

import aipds.app as app_module
from aipds.models import AgentEvent
from aipds.proto.host import TOKEN_FILENAME, HostInfo
from aipds.routes.proto_public import cookie_name
from aipds.proto.limits import BuildSemaphore
from aipds.workspace import Workspace
from fakes.fake_runner import FakeRunner
from fakes.in_memory_s3 import FakeS3Store

from aipds.turn_job import TurnJobs

client: TestClient


@pytest.fixture(autouse=True)
def _live_client():
    """요청 사이에 이벤트 루프를 유지하는 클라이언트 — 빌드 턴은 요청이 아니라 세션의
    서버 작업이다(aipds/turn_job.py). `with` 없는 TestClient는 요청마다 루프를 새로
    열고 닫아, POST가 시작한 턴이 다음 요청 전에 루프와 함께 사라진다."""
    global client
    with TestClient(app_module.app) as live:
        client = live
        yield

PID = "proto-route-test"
SLUG = "demo"
SPEC_KEY = f"aiplc-docs/discovery/prototypes/{SLUG}/PROTOTYPE-{SLUG}.md"


def _build(text: str) -> str:
    """빌드 턴을 시작하고 끝까지 본다. SSE 본문 전체를 돌려준다."""
    r = client.post(f"/projects/{PID}/prototypes/{SLUG}/turns", json={"text": text})
    assert r.status_code == 200, r.text
    with client.stream("GET", f"/projects/{PID}/prototypes/{SLUG}/events",
                       params={"turn": r.json()["turn_id"]}) as resp:
        assert resp.status_code == 200
        return "".join(resp.iter_text())


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
        self._opened = False
        self._events = events or [AgentEvent(kind="message", text="building"),
                                  AgentEvent(kind="done")]
        self._start_exc = start_exc
        self.turns = TurnJobs(retention=float("inf"))

    #: 실물과 같은 사실을 갖는다: 개시 턴이 이미 돌았는가. 라우트가 이 값으로
    #: 사용자의 첫 메시지를 개시 프롬프트로 감쌀지 정하므로, 가짜가 이것을
    #: 갖지 않으면 그 분기를 검증할 수 없다.
    @property
    def opened(self) -> bool:
        return self._opened

    def first_prompt(self, request=None) -> str:
        # 요청이 실렸는지를 단정할 수 있게 인자를 반환값에 노출한다.
        return f"FIRST({request})" if request is not None else "FIRST(None)"

    async def start(self):
        if self._start_exc is not None:
            raise self._start_exc
        self.started = True
        self.status = "ready"

    async def send_message(self, text):
        self.messages.append(text)
        self._opened = True
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
        # Mirrors the real PrototypeSession.close() contract: it releases the
        # build slot it was started under. The route (reset and close_session
        # alike) relies on close() itself to free the slot rather than
        # releasing it a second time, so the fake has to hold up its end --
        # INCLUDING the guard against a second close(). BuildSemaphore.release()
        # clamps at zero and cannot detect an over-release, so a second call
        # would free a slot belonging to some OTHER session. The real
        # PrototypeSession.close() guards this twice (session.py:221-223,
        # :245-247); a fake with neither guard is strictly more forgiving than
        # what it stands in for, on exactly the axis that matters here.
        if self.closed:
            return
        self.closed = True
        self.status = "closed"
        app_module.build_semaphore.release()


class FakeProtoHost:
    def __init__(self, root=None):
        # Real ProtoHost.purge() deletes {root}/{pid}/{slug} off disk and
        # raises if anything survives -- the reset route's own idempotency
        # and "S3-before-local" tests need that actually happening, not just
        # bookkeeping, so the fake is handed the same tmp_path the fixture
        # points app_module._proto_root() at.
        self._root = root
        self.infos: dict[tuple[str, str], HostInfo] = {}
        self.start_exc = None
        # Mirrors start_exc for the purge path. Without it the route's
        # `failures.append("build-tree")` branch was unreachable from any test:
        # deleting that line left all 60 route tests green, i.e. a partial reset
        # silently reported as success -- the one outcome the retry-convergence
        # invariant forbids. The real ProtoHost.purge() raises RuntimeError
        # whenever residue survives its rmtree (test_proto_host.py pins that),
        # so a fake that cannot raise is strictly more forgiving than what it
        # stands in for, on exactly the axis under test.
        self.purge_exc = None
        self.stopped: list[tuple[str, str]] = []
        # Recorded so a test can assert WHICH directory hosting was pointed at.
        # The real ProtoHost defaults to {root}/{pid}/{slug} when cwd is None,
        # but the build output lives one level down in prototype/ -- dropping
        # cwd here is what let that mismatch ship (npm ENOENT on package.json).
        self.start_cwds: list[object] = []
        # The public proxy prefix the build must be told about -- Next.js bakes
        # basePath in at build time, so a missing value here is a 404 on every
        # asset, not a recoverable runtime detail.
        self.start_base_paths: list[object] = []
        # 프로토타입 앱의 런타임 LLM 호출이 쓸 모델. 라우트가 이것을 넘기지
        # 않으면 빌드된 앱이 에이전트가 임의로 고른 모델로 돌아, 사용자가
        # 프로젝트에서 고른 값이 무시된다.
        self.start_model_ids: list[object] = []
        self.purged: list[tuple[str, str]] = []

    async def start(self, pid, slug, cwd=None, base_path=None, model_id=None):
        self.start_cwds.append(cwd)
        self.start_base_paths.append(base_path)
        self.start_model_ids.append(model_id)
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

    async def purge(self, pid, slug):
        self.purged.append((pid, slug))
        if self.purge_exc is not None:
            # Raise BEFORE deleting, like the real one: it validates its input
            # and calls stop() first, and its own residue check raises only
            # when the tree is still there. A fake that wiped the tree and then
            # raised would let a test assert a survivor that the real code
            # cannot deliver.
            raise self.purge_exc
        self.infos.pop((pid, slug), None)
        if self._root is not None:
            import shutil
            shutil.rmtree(self._root / pid / slug, ignore_errors=True)

    def status(self, pid, slug):
        return self.infos.get((pid, slug))

    def slugs(self, pid):
        """실물 ProtoHost.slugs와 같은 합집합(디렉토리 + 호스팅 레지스트리 +
        토큰 파일). 프로젝트 삭제가 "이 프로젝트의 모든 슬러그"를 여기서 얻는다."""
        found = {slug for (p, slug) in self.infos if p == pid}
        if self._root is not None:
            base = self._root / pid
            if base.is_dir():
                found |= {child.name for child in base.iterdir() if child.is_dir()}
        return sorted(found)

    def log_tail(self, pid, slug, lines=100):
        info = self.infos.get((pid, slug))
        return info.log_tail if info else ""

    # ---- access tokens ----
    #
    # 실제 파일을 쓴다(가짜 카운터가 아니라). 이 fake의 root는 fixture가
    # app_module._proto_root()에 물린 tmp_path와 같은 값이므로, 토큰 파일이 실제
    # 프로토타입 트리에 놓인다 -- 그래서 "아카이브 zip에 토큰이 실리지 않는다"를
    # 단정하는 테스트가 진짜 파일을 상대로 검사하게 된다. 인메모리 dict로 두면
    # 그 테스트는 아무것도 검증하지 않는 채로 통과한다.
    def _token_path(self, pid, slug):
        return self._root / pid / slug / TOKEN_FILENAME

    def ensure_token(self, pid, slug):
        path = self._token_path(pid, slug)
        try:
            existing = path.read_text(encoding="utf-8").strip()
        except OSError:
            existing = ""
        if existing:
            return existing
        token = f"token-{pid}-{slug}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token, encoding="utf-8")
        return token

    def token_for(self, pid, slug):
        try:
            return self._token_path(pid, slug).read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def resolve_token(self, token):
        for path in self._root.glob(f"*/*/{TOKEN_FILENAME}"):
            if path.read_text(encoding="utf-8").strip() == token:
                return (path.parent.parent.name, path.parent.name)
        return None


@pytest.fixture()
def proto_env(monkeypatch, tmp_path):
    """Registered project + fake S3/session/host wiring."""
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")
    # VM env vars are gone -- the session route's config guard now checks a
    # build slot, not an image ARN.
    #
    # TWO fakes, deliberately, matching test_routes_surveys.py. These are
    # different S3 namespaces in production: s3_store_factory is prefixed
    # `projects/{pid}/` while surveys_root_s3_factory is bucket-root (prefix
    # ""), because the token index has to be readable before the project is
    # known. Pointing both at ONE fake collapsed that distinction and made the
    # token-index assertions vacuous -- mutating SurveyStore.purge's
    # `self._root.delete_prefix` to `self._s3.delete_prefix` (i.e. writing the
    # root-scoped key into the project prefix, which in production leaves the
    # real index untouched and every /survey/{token} link live) kept all 60
    # route tests green.
    fake_s3 = FakeS3Store()
    fake_root_s3 = FakeS3Store()

    async def fake_make_workspace(pid):
        return Workspace(FakeRunner(FakeS3Store()))

    monkeypatch.setattr(app_module, "make_workspace", fake_make_workspace)
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: fake_s3)
    # SurveyStore.purge() (exercised by the reset route) also reads/writes the
    # bucket-root token index via surveys_root_s3_factory(); left unpatched it
    # falls through to a real boto3 client with AIPDS_S3_BUCKET="" and
    # blows up on bucket-name validation before it ever reaches the fake.
    monkeypatch.setattr(app_module, "surveys_root_s3_factory",
                        lambda: fake_root_s3)
    # list_prototypes' "built" signal reads the local build dir straight off
    # disk (app_module._proto_root() / pid / slug) -- point it at tmp_path so
    # tests never touch the real ~/aipds-protos, matching
    # test_routes_prototypes_archive.py's fixture.
    monkeypatch.setattr(app_module, "_proto_root", lambda: tmp_path)

    fake_host = FakeProtoHost(root=tmp_path)
    monkeypatch.setattr(app_module, "proto_host", lambda: fake_host)
    monkeypatch.setattr(app_module, "build_semaphore",
                        BuildSemaphore(max_concurrent=2))

    sessions_backup = dict(app_module.proto_sessions)
    app_module.proto_sessions.clear()

    resp = client.post("/projects", json={"project_id": PID})
    assert resp.status_code in (200, 201, 409)

    # "root_s3" is exposed so token-index assertions can name the namespace the
    # index actually lives in rather than borrowing the project store's.
    yield {"s3": fake_s3, "root_s3": fake_root_s3, "host": fake_host,
           "root": tmp_path}

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
    # access_url이 None인 것이 이 페이로드의 일부다: 호스팅되지 않은 프로토타입에는
    # 공유할 링크가 없고, 프론트는 이 값의 부재로 버튼을 감춘다.
    assert body["prototypes"] == [{"slug": SLUG, "name": None,
                                   "spec_path": SPEC_KEY,
                                   "state": "none", "port": None,
                                   "session_open": False,
                                   "preview_stale": False,
                                   "access_url": None,
                                   "response_count": 0,
                                   "has_survey": False}]


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
    # A live session must still take a turn (404 would mean "no session").
    _build("hi")


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


# ---- 호스팅 상태와 세션 상태는 서로 다른 사실이다 ----
#
# `state` 하나가 두 가지를 실어 나르고 있었다: 산출물·서버가 어떤 상태인가, 그리고
# 빌드 세션이 열려 있는가. 그 둘은 독립적인데 열거형이 하나뿐이라 세션이 호스팅을
# 가렸다 — 실행 중인 프로토타입을 수정하기 시작하면 카드가 `building`으로 바뀌어
# 프리뷰·공유 링크 버튼이 사라졌고, 정작 서버는 계속 떠 있었다. 그래서 사용자는
# 프리뷰를 본 직후(수정하고 싶어지는 그 순간) 수정을 시작하면 방금 보던 것을 잃었다.
#
# 이제 `state`는 산출물과 서버만 말하고, 세션이 열려 있는지는 `session_open`이
# 따로 말한다.

def test_a_running_host_is_not_hidden_by_an_open_build_session(proto_env, monkeypatch):
    """실행 중인 프로토타입을 수정하는 동안에도 카드가 실행 중임을 알아야 한다.

    세션을 여는 것은 호스팅을 건드리지 않는다(어느 라우트도 stop을 부르지 않는다)
    — 서버는 실제로 계속 떠 있으므로, 화면이 아니라고 말하는 것이 틀린 것이다.
    프리뷰와 공유 링크는 참가자에게 이미 나가 있는 주소이고 그 링크는 계속 산다."""
    _seed_spec(proto_env["s3"])
    proto_dir = proto_env["root"] / PID / SLUG / "prototype"
    proto_dir.mkdir(parents=True)
    (proto_dir / "app.js").write_text("console.log(1)", encoding="utf-8")
    proto_env["host"].infos[(PID, SLUG)] = HostInfo(state="running", port=4007,
                                                    log_tail="")
    session = FakePrototypeSession()
    session.status = "building"
    app_module.proto_sessions[(PID, SLUG)] = session

    entry = client.get(f"/projects/{PID}/prototypes").json()["prototypes"][0]

    assert entry["state"] == "running"
    assert entry["port"] == 4007
    # 그러면서 세션이 열려 있다는 사실도 잃지 않는다 — 카드의 빌드 버튼이 이
    # 값으로 "수정하기"와 "세션 열기"를 가른다.
    assert entry["session_open"] is True


def test_the_share_url_survives_a_modify_session(proto_env, monkeypatch):
    """`access_url`은 `state == "running"`일 때만 실린다. 세션이 호스팅을 가리던
    동안에는 수정을 시작하는 순간 참가자용 링크가 카드에서 사라졌다 — 서버는 그
    링크로 계속 응답하고 있었으므로 화면만 거짓말을 했다."""
    _seed_spec(proto_env["s3"])
    proto_dir = proto_env["root"] / PID / SLUG / "prototype"
    proto_dir.mkdir(parents=True)
    (proto_dir / "app.js").write_text("console.log(1)", encoding="utf-8")
    proto_env["host"].infos[(PID, SLUG)] = HostInfo(state="running", port=4007,
                                                    log_tail="")
    proto_env["host"].ensure_token(PID, SLUG)
    session = FakePrototypeSession()
    session.status = "building"
    app_module.proto_sessions[(PID, SLUG)] = session

    entry = client.get(f"/projects/{PID}/prototypes").json()["prototypes"][0]

    assert entry["access_url"] is not None


def test_session_open_is_false_without_a_working_session(proto_env):
    """완료·실패한 세션은 열려 있는 것이 아니다. `_WORKING_STATUSES`와 같은
    질문이어야 한다 — 그렇지 않으면 끝난 세션이 카드의 버튼을 영구히
    "세션 열기"로 붙들어 둔다(그 결함이 이 파일 위쪽에 이미 기록돼 있다)."""
    _seed_spec(proto_env["s3"])
    body = client.get(f"/projects/{PID}/prototypes").json()
    assert body["prototypes"][0]["session_open"] is False

    for status in ("complete", "failed", "ready"):
        session = FakePrototypeSession()
        session.status = status
        app_module.proto_sessions[(PID, SLUG)] = session
        entry = client.get(f"/projects/{PID}/prototypes").json()["prototypes"][0]
        assert entry["session_open"] is False, status


def test_session_open_is_true_while_work_is_in_flight(proto_env):
    _seed_spec(proto_env["s3"])
    for status in ("starting", "building", "waiting_input"):
        session = FakePrototypeSession()
        session.status = status
        app_module.proto_sessions[(PID, SLUG)] = session
        entry = client.get(f"/projects/{PID}/prototypes").json()["prototypes"][0]
        assert entry["session_open"] is True, status


# ---- 떠 있는 프리뷰가 이전 버전인가 ----
#
# 실행 중에 수정할 수 있게 되면서, 서버는 그대로 뜬 채 소스만 바뀌는 구간이 생겼다.
# 그때 카드가 "실행 :4007"만 말하면 사용자는 자기 수정이 반영됐다고 읽는다 — 참가자
# 링크가 보여 주는 것은 이전 버전이고, 다시 호스팅해야 바뀐다.
#
# **세션이 열려 있는지로 대신하면 안 된다**: 세션이 닫히는 순간 그 신호가 사라지고,
# 정작 그때가 오해하기 가장 쉬운 시점이다. 그래서 서버가 소스를 읽은 시각과 빌드
# 트리의 최신 소스 mtime을 비교한다.

def test_a_freshly_hosted_preview_is_not_stale(proto_env):
    _seed_spec(proto_env["s3"])
    proto_dir = proto_env["root"] / PID / SLUG / "prototype"
    proto_dir.mkdir(parents=True)
    (proto_dir / "app.js").write_text("console.log(1)", encoding="utf-8")
    proto_env["host"].infos[(PID, SLUG)] = HostInfo(
        state="running", port=4007, log_tail="", built_at=time.time() + 5)

    entry = client.get(f"/projects/{PID}/prototypes").json()["prototypes"][0]

    assert entry["preview_stale"] is False


def test_a_source_change_after_hosting_makes_the_preview_stale(proto_env):
    _seed_spec(proto_env["s3"])
    proto_dir = proto_env["root"] / PID / SLUG / "prototype"
    proto_dir.mkdir(parents=True)
    (proto_dir / "app.js").write_text("console.log(1)", encoding="utf-8")
    # 서버는 소스보다 먼저 떴다.
    proto_env["host"].infos[(PID, SLUG)] = HostInfo(
        state="running", port=4007, log_tail="", built_at=time.time() - 60)

    entry = client.get(f"/projects/{PID}/prototypes").json()["prototypes"][0]

    assert entry["preview_stale"] is True


def test_only_a_running_preview_can_be_stale(proto_env):
    """호스팅되지 않은 프로토타입에는 낡을 프리뷰가 없다 — 거기에 경고를 띄우면
    사용자가 무엇을 해야 하는지 알 수 없다(다시 호스팅할 대상이 없다)."""
    _seed_spec(proto_env["s3"])
    proto_dir = proto_env["root"] / PID / SLUG / "prototype"
    proto_dir.mkdir(parents=True)
    (proto_dir / "app.js").write_text("console.log(1)", encoding="utf-8")

    entry = client.get(f"/projects/{PID}/prototypes").json()["prototypes"][0]

    assert entry["state"] == "built"
    assert entry["preview_stale"] is False


def test_staleness_survives_the_session_closing(proto_env):
    """이것이 이 필드가 존재하는 이유다. 세션이 닫힌 뒤에도 프리뷰는 여전히 이전
    버전이고, 바로 그때 사용자가 "반영됐다"고 오해한다."""
    _seed_spec(proto_env["s3"])
    proto_dir = proto_env["root"] / PID / SLUG / "prototype"
    proto_dir.mkdir(parents=True)
    (proto_dir / "app.js").write_text("console.log(1)", encoding="utf-8")
    proto_env["host"].infos[(PID, SLUG)] = HostInfo(
        state="running", port=4007, log_tail="", built_at=time.time() - 60)
    # 세션은 없다(닫혔다).
    entry = client.get(f"/projects/{PID}/prototypes").json()["prototypes"][0]

    assert entry["session_open"] is False
    assert entry["preview_stale"] is True


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


def test_list_carries_the_human_readable_name_from_the_spec(proto_env):
    """카드 제목의 출처다. 슬러그만으로는 Path A.1의 제목이 항상 리터럴
    "prototype"이었다(proto/layout.py의 `SINGLE_ID`) — 제품명은 명세 본문에만
    있었고 목록 응답에는 실려 오지 않았다."""
    proto_env["s3"].blobs[SPEC_KEY] = (
        "# Prototype Specification\n\n- **Product**: 기획전 AI 어시스턴트\n")
    body = client.get(f"/projects/{PID}/prototypes").json()
    assert body["prototypes"][0]["name"] == "기획전 AI 어시스턴트"


def test_list_name_is_null_when_the_spec_does_not_name_the_prototype(proto_env):
    """실측 Path B 산출물에는 이름 줄이 없다. 그때 프론트가 슬러그로 되돌아가야
    하므로, 부재가 값의 부재로 표현되어야 한다."""
    _seed_spec(proto_env["s3"])
    assert client.get(f"/projects/{PID}/prototypes").json()[
        "prototypes"][0]["name"] is None


def test_list_survives_a_spec_that_cannot_be_read(proto_env, monkeypatch):
    """`discover`는 `list`로 키를 찾고 이름은 `get`으로 읽으므로 그 사이에
    리셋·삭제가 끼어들 수 있다. 이름 하나 때문에 목록 전체가 500이 되면
    안 된다 — 카드는 이름 없이도 완전히 동작한다."""
    _seed_spec(proto_env["s3"])

    async def vanished(key):
        raise FileNotFoundError(key)

    monkeypatch.setattr(proto_env["s3"], "get", vanished)

    resp = client.get(f"/projects/{PID}/prototypes")
    assert resp.status_code == 200
    assert resp.json()["prototypes"][0]["name"] is None
    assert resp.json()["prototypes"][0]["state"] == "none"


def test_list_survives_any_failure_to_read_the_spec(proto_env, monkeypatch):
    """사라진 키만이 실패 모드가 아니다 — S3 5xx, 디코딩 실패도 같은 자리에서
    난다. 어느 쪽이든 목록은 이름 없이 성공해야 한다: 카드의 상태·버튼은 이 값에
    의존하지 않으므로, 장식 하나를 위해 탭 전체를 잃는 것이 더 나쁜 결과다."""
    _seed_spec(proto_env["s3"])

    async def boom(key):
        raise RuntimeError("s3 is having a day")

    monkeypatch.setattr(proto_env["s3"], "get", boom)

    resp = client.get(f"/projects/{PID}/prototypes")
    assert resp.status_code == 200
    assert resp.json()["prototypes"][0]["name"] is None


def test_list_reports_survey_response_count(proto_env):
    """The reset confirmation needs the count at button-press time, so it rides
    the list rather than costing an extra request."""
    _seed_spec(proto_env["s3"])
    for name in ("r1", "r2", "r3"):
        proto_env["s3"].blobs[
            f"prototypes/{SLUG}/survey/responses/{name}.json"] = "{}"

    body = client.get(f"/projects/{PID}/prototypes").json()

    assert body["prototypes"][0]["response_count"] == 3


def test_list_reports_zero_responses_when_there_is_no_survey(proto_env):
    _seed_spec(proto_env["s3"])
    body = client.get(f"/projects/{PID}/prototypes").json()
    assert body["prototypes"][0]["response_count"] == 0


def test_list_says_whether_a_survey_exists_at_all(proto_env):
    """응답 수로는 표현할 수 없는 구분이다 — 설문 없음도 0, 응답 0건도 0이다.

    실측 test2222: 프로토타입 3개 중 1개만 설문이 있었는데 카드에 그 사실이 없어
    나머지 둘의 설문이 빠진 것을 알아차릴 방법이 없었다.
    """
    _seed_spec(proto_env["s3"])
    assert client.get(f"/projects/{PID}/prototypes").json()[
        "prototypes"][0]["has_survey"] is False

    proto_env["s3"].blobs[f"prototypes/{SLUG}/survey/questionnaire.json"] = "{}"
    assert client.get(f"/projects/{PID}/prototypes").json()[
        "prototypes"][0]["has_survey"] is True


def test_list_reports_a_survey_that_has_no_answers_yet(proto_env):
    """설문은 있고 응답은 0건 — 카드가 "설문 없음"으로 오인하면 안 된다."""
    _seed_spec(proto_env["s3"])
    proto_env["s3"].blobs[f"prototypes/{SLUG}/survey/questionnaire.json"] = "{}"
    info = client.get(f"/projects/{PID}/prototypes").json()["prototypes"][0]
    assert info["has_survey"] is True and info["response_count"] == 0


def test_list_counts_archived_responses_because_a_reset_destroys_them(proto_env):
    """The regression this guards: the reset dialog claimed "nothing to lose"
    over a dozen real submissions.

    archive_current() MOVES a closed round's answers to
    survey/archive/{closed_at}/responses/, and SurveyStore.purge() deletes the
    whole survey/ tree -- archive included. Counting only the live responses/
    prefix reported 0 for a prototype whose survey had been regenerated after a
    first round (the documented normal flow), and 0 takes neither the dialog's
    rose (`> 0`) branch nor its amber (`=== null`) branch: the user saw a bare
    "검증 설문" bullet, no count and no irreversibility warning, and then 12
    answers were destroyed."""
    _seed_spec(proto_env["s3"])
    for i in range(12):
        proto_env["s3"].blobs[
            f"prototypes/{SLUG}/survey/archive/2026-01-01T00:00:00Z/"
            f"responses/a{i}.json"] = "{}"

    body = client.get(f"/projects/{PID}/prototypes").json()

    assert body["prototypes"][0]["response_count"] == 12


def test_list_response_count_matches_what_the_reset_actually_destroys(proto_env):
    """The number the dialog shows and the number that disappears have to be
    the same one. Both rounds present -- live and archived -- then reset, and
    nothing under any responses/ survives."""
    _seed_spec(proto_env["s3"])
    s3 = proto_env["s3"]
    s3.blobs[f"prototypes/{SLUG}/survey/responses/r1.json"] = "{}"
    s3.blobs[f"prototypes/{SLUG}/survey/archive/2026-01-01T00:00:00Z/"
             f"responses/a1.json"] = "{}"
    s3.blobs[f"prototypes/{SLUG}/survey/archive/2026-01-01T00:00:00Z/"
             f"responses/a2.json"] = "{}"

    reported = client.get(
        f"/projects/{PID}/prototypes").json()["prototypes"][0]["response_count"]
    destroyed = len([k for k in s3.blobs if "/responses/" in k])

    assert client.delete(f"/projects/{PID}/prototypes/{SLUG}").status_code == 204

    assert reported == destroyed == 3
    assert [k for k in s3.blobs if "/responses/" in k] == []


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
    # detail은 안정적 코드다 — 문구는 프론트 딕셔너리가 소유한다.
    assert second.json()["detail"] == "build_slots_busy"


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


def test_turns_and_events_without_a_session_404(proto_env):
    assert client.post(f"/projects/{PID}/prototypes/{SLUG}/turns",
                       json={"text": "hi"}).status_code == 404
    assert client.get(f"/projects/{PID}/prototypes/{SLUG}/events",
                      params={"turn": "t"}).status_code == 404


def test_events_streams_and_redacts(proto_env):
    session = FakePrototypeSession(events=[
        AgentEvent(kind="message",
                   text="key=AKIAIOSFODNN7EXAMPLE1 done"),
        AgentEvent(kind="done"),
    ])
    session.status = "ready"
    app_module.proto_sessions[(PID, SLUG)] = session
    text = _build("build please")
    events = _sse_events(text)
    assert [e["kind"] for e in events] == ["message", "done"]
    assert "AKIAIOSFODNN7EXAMPLE1" not in text
    # 세션의 첫 메시지이므로 개시 프롬프트로 감싸진다(그 분기는 아래 전용
    # 테스트들이 검증한다). 이 테스트의 대상은 스트리밍과 리댁션이다.
    assert session.messages == ["FIRST(build please)"]


def test_events_first_sentinel_uses_first_prompt(proto_env):
    session = FakePrototypeSession()
    session.status = "ready"
    app_module.proto_sessions[(PID, SLUG)] = session
    _build("__first__")
    assert session.messages == ["FIRST(None)"]


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


# ---- reset ----

def _seed_everything(proto_env, monkeypatch=None):
    """All eight places one prototype leaves state, plus a sibling prototype
    and the shared results doc that must both survive.

    The eighth -- surveys/by-token/{token}.json -- is the ROOT-scoped token
    index the ordering constraint exists to protect: it is the one thing
    SurveyStore.purge() cannot recover once the questionnaire that names its
    token is gone (Task 1's `_collect_tokens()` learns tokens by reading
    questionnaires, there is no reverse lookup). Omitting it here would make
    every reset test pass regardless of whether survey purge actually runs,
    and did exactly that until this was added.

    It is seeded into `root_s3`, NOT `s3`: `surveys/by-token/` is bucket-root
    while everything else here is under `projects/{pid}/`. Seeding both from one
    fake made those assertions vacuous (see the fixture's note)."""
    s3 = proto_env["s3"]
    root_s3 = proto_env["root_s3"]
    _seed_spec(s3)
    s3.blobs[f"prototypes/{SLUG}/session.json"] = '{"session_id": "x"}'
    s3.blobs[f"prototypes/{SLUG}/transcript/00000001.jsonl"] = "{}"
    s3.blobs[f"prototypes/{SLUG}/bundle/package.json"] = "{}"
    s3.blobs[f"prototypes/{SLUG}/survey/questionnaire.json"] = json.dumps(
        {"slug": SLUG, "project_id": PID,
         "token": "tok-1",  # nosec B105 - a survey share token in a fixture
         "status": "open", "closed_at": None, "questions": []})
    s3.blobs[f"prototypes/{SLUG}/survey/responses/r1.json"] = "{}"
    root_s3.blobs["surveys/by-token/tok-1.json"] = json.dumps(
        {"project_id": PID, "slug": SLUG})
    s3.blobs[f"aiplc-docs/discovery/prototypes/{SLUG}/validation-questionnaire.md"] = "# q"
    s3.blobs[f"aiplc-docs/discovery/prototypes/{SLUG}/validation-results.md"] = "# mine"
    # 다른 프로토타입(단수 레이아웃)의 결과 문서. 리셋이 남의 것을 지우지
    # 않는다는 것을 지킨다.
    s3.blobs["aiplc-docs/discovery/prototype/validation-results.md"] = "# other"
    s3.blobs["prototypes/other/session.json"] = '{"session_id": "y"}'
    proto_dir = proto_env["root"] / PID / SLUG / "prototype"
    proto_dir.mkdir(parents=True)
    (proto_dir / "package.json").write_text("{}", encoding="utf-8")


def test_reset_clears_everything_but_keeps_the_spec(proto_env, monkeypatch):
    _seed_everything(proto_env, monkeypatch)
    s3 = proto_env["s3"]

    assert client.delete(
        f"/projects/{PID}/prototypes/{SLUG}").status_code == 204

    assert [k for k in s3.blobs if k.startswith(f"prototypes/{SLUG}/")] == []
    assert f"aiplc-docs/discovery/prototypes/{SLUG}/validation-questionnaire.md" \
        not in s3.blobs
    # Root-scoped -- a DIFFERENT S3 namespace, which is why it is asserted
    # against root_s3 and not the prefix filter above. This is the index a
    # failed/misordered purge would strand permanently.
    assert "surveys/by-token/tok-1.json" not in proto_env["root_s3"].blobs
    assert not (proto_env["root"] / PID / SLUG).exists()
    # 이 프로토타입의 검증 결과는 사라진다 — 리셋한 프로토타입의 결과가 남으면
    # 같은 슬러그로 다시 만든 프로토타입의 것으로 읽힌다.
    assert f"aiplc-docs/discovery/prototypes/{SLUG}/validation-results.md" \
        not in s3.blobs
    # Survivors: the spec (or the card disappears), ANOTHER prototype's results
    # doc, and any other prototype's state.
    assert s3.blobs[SPEC_KEY] == "# PROTOTYPE demo"
    assert s3.blobs["aiplc-docs/discovery/prototype/validation-results.md"] == "# other"
    assert s3.blobs["prototypes/other/session.json"] == '{"session_id": "y"}'


def test_reset_leaves_the_card_listable_as_none(proto_env, monkeypatch):
    """The point of keeping the spec: the card comes back as a fresh, buildable
    prototype rather than vanishing."""
    _seed_everything(proto_env, monkeypatch)

    client.delete(f"/projects/{PID}/prototypes/{SLUG}")

    body = client.get(f"/projects/{PID}/prototypes").json()
    assert body["prototypes"] == [{"slug": SLUG, "name": None,
                                   "spec_path": SPEC_KEY,
                                   "state": "none", "port": None,
                                   "session_open": False,
                                   "preview_stale": False,
                                   "access_url": None,
                                   "response_count": 0,
                                   "has_survey": False}]


def test_reset_closes_a_live_session_and_frees_its_build_slot(proto_env, monkeypatch):
    """A live session is cleaned up rather than refused -- 'reset' should not
    make the user close things first. close() releases the build semaphore, so
    the slot must come back too."""
    _seed_spec(proto_env["s3"])
    session = FakePrototypeSession()
    _install_session_factory(monkeypatch, session)
    client.post(f"/projects/{PID}/prototypes/{SLUG}/session")
    assert app_module.build_semaphore.snapshot()["active_builds"] == 1

    assert client.delete(
        f"/projects/{PID}/prototypes/{SLUG}").status_code == 204

    assert session.closed
    assert (PID, SLUG) not in app_module.proto_sessions
    assert app_module.build_semaphore.snapshot()["active_builds"] == 0


def test_reset_stops_hosting(proto_env, monkeypatch):
    _seed_everything(proto_env, monkeypatch)
    proto_env["host"].infos[(PID, SLUG)] = HostInfo(
        state="running", port=4001, log_tail="")

    assert client.delete(
        f"/projects/{PID}/prototypes/{SLUG}").status_code == 204

    assert (PID, SLUG) in proto_env["host"].purged


def test_reset_without_a_session_succeeds(proto_env, monkeypatch):
    """Unlike DELETE .../session (404 when absent), a missing session is the
    NORMAL case here -- a finished build has already been evicted."""
    _seed_spec(proto_env["s3"])

    assert client.delete(
        f"/projects/{PID}/prototypes/{SLUG}").status_code == 204


def test_reset_is_idempotent(proto_env, monkeypatch):
    _seed_everything(proto_env, monkeypatch)

    assert client.delete(f"/projects/{PID}/prototypes/{SLUG}").status_code == 204
    assert client.delete(f"/projects/{PID}/prototypes/{SLUG}").status_code == 204


def test_reset_502_when_a_purge_fails_and_keeps_local_state(proto_env, monkeypatch):
    """S3 before local, so a failure leaves the card reading 'built' -- the
    incomplete reset stays visible. Wiping local first would flip the card to
    'none' and tell the user it finished while S3 still held the survey."""
    _seed_everything(proto_env, monkeypatch)

    async def boom(self):
        raise RuntimeError("s3 down")

    monkeypatch.setattr(
        "aipds.survey.store.SurveyStore.purge", boom, raising=True)

    resp = client.delete(f"/projects/{PID}/prototypes/{SLUG}")

    assert resp.status_code == 502
    # detail은 `코드:진단정보` 형태다 — 프론트(errorMessage.ts)가 콜론 앞을
    # 코드로 번역하고 뒤를 괄호로 덧붙인다. 구분자가 바뀌면 코드를 못 찾아
    # 사용자가 번역되지 않은 원문을 본다.
    assert resp.json()["detail"] == "init_incomplete:survey"
    assert (proto_env["root"] / PID / SLUG).is_dir()


def test_reset_502_survey_failure_also_skips_session_state_and_converges_on_retry(
        proto_env, monkeypatch):
    """The gate one layer up from the local-tree one: session-state deletes
    prototypes/{slug}/ wholesale, a SUPERSET of the survey tree survey.purge()
    reads its tokens from. Running it after a failed survey purge would
    destroy those questionnaires and strand surveys/by-token/{token}.json
    permanently -- a retry's _collect_tokens() would find nothing and report
    204 over a token that still resolves to this prototype (a stale
    /survey/{token} link becomes a live credential into whatever survey the
    slug gets next). So session-state must be skipped, not just delayed,
    whenever survey purge fails -- and the retry must still converge once
    survey purge is allowed to succeed."""
    _seed_everything(proto_env, monkeypatch)
    s3 = proto_env["s3"]
    from aipds.survey.store import SurveyStore
    real_purge = SurveyStore.purge

    calls = {"n": 0}

    async def fail_once_then_real(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("s3 down")
        await real_purge(self)

    monkeypatch.setattr(
        "aipds.survey.store.SurveyStore.purge", fail_once_then_real,
        raising=True)

    first = client.delete(f"/projects/{PID}/prototypes/{SLUG}")
    assert first.status_code == 502
    # session-state must have been SKIPPED: the questionnaire survey.purge()
    # needs to reclaim its token is still there, and so is the token index.
    assert f"prototypes/{SLUG}/survey/questionnaire.json" in s3.blobs
    assert "surveys/by-token/tok-1.json" in proto_env["root_s3"].blobs
    assert (proto_env["root"] / PID / SLUG).is_dir()  # local also skipped

    second = client.delete(f"/projects/{PID}/prototypes/{SLUG}")
    assert second.status_code == 204
    assert f"prototypes/{SLUG}/survey/questionnaire.json" not in s3.blobs
    assert "surveys/by-token/tok-1.json" not in proto_env["root_s3"].blobs
    assert not (proto_env["root"] / PID / SLUG).exists()


def test_reset_unknown_project_404(proto_env):
    assert client.delete("/projects/nope/prototypes/demo").status_code == 404


# ---- every failure is REPORTED as one (the 502 branches) ----
#
# Three of the four `failures.append(...)` lines were dead to the tests:
# deleting "build-tree", "session" or "session-state" individually left all 60
# route tests green. Each deletion converts a PARTIAL reset into a reported
# success -- the single outcome the retry-convergence invariant forbids, since
# a 204 is exactly what tells the user (and the UI's reload) that nothing more
# needs doing. The tests below cover one branch each and were each verified to
# fail with its own append line removed.

def test_reset_502_when_the_build_tree_purge_fails(proto_env):
    """The `build-tree` branch. The real ProtoHost.purge() raises whenever
    residue survives its rmtree -- a permission error deep in node_modules is
    the realistic producer -- and reporting 204 over it would leave a half-
    deleted tree that the card still calls 빌드 완료, forever, because the user
    was told the reset finished."""
    _seed_everything(proto_env)
    proto_env["host"].purge_exc = RuntimeError("purge left residue")

    resp = client.delete(f"/projects/{PID}/prototypes/{SLUG}")

    assert resp.status_code == 502
    assert "build-tree" in resp.json()["detail"]
    # It was attempted, and its failure is what the 502 reports -- not a skip.
    assert (PID, SLUG) in proto_env["host"].purged
    assert (proto_env["root"] / PID / SLUG).is_dir()


def test_reset_502_when_the_session_close_fails(proto_env, monkeypatch):
    """The `session` branch. close() releases the build slot, so a swallowed
    failure here reports success over a slot that stays held until the process
    restarts -- and with AIPDS_PROTO_MAX_CONCURRENT capping a workshop box
    at 2, that is a real 429 for another team."""
    _seed_spec(proto_env["s3"])
    session = FakePrototypeSession()
    _install_session_factory(monkeypatch, session)
    client.post(f"/projects/{PID}/prototypes/{SLUG}/session")

    async def boom():
        raise RuntimeError("builder wedged")

    monkeypatch.setattr(session, "close", boom)

    resp = client.delete(f"/projects/{PID}/prototypes/{SLUG}")

    assert resp.status_code == 502
    assert "session" in resp.json()["detail"]


def test_reset_502_when_the_session_state_purge_fails(proto_env, monkeypatch):
    """The `session-state` branch. It is the step that deletes the transcript
    and session.json, so a swallowed failure reports a finished reset over build
    chatter and a resumable session id that are both still there -- and the next
    build would resume the very context the reset promised to clear."""
    _seed_everything(proto_env)

    async def boom(s3, slug):
        raise RuntimeError("s3 down")

    monkeypatch.setattr("aipds.routes.prototypes.purge_session_state", boom)

    resp = client.delete(f"/projects/{PID}/prototypes/{SLUG}")

    assert resp.status_code == 502
    assert "session-state" in resp.json()["detail"]
    # Local is gated behind it, so the card stays "built" -- visibly incomplete.
    assert (proto_env["root"] / PID / SLUG).is_dir()


def test_reset_502_when_a_token_cannot_be_reclaimed(proto_env):
    """End to end for SurveyStore._collect_tokens' raise: an unparseable
    questionnaire must produce a 502, not a silent 204.

    Continuing past it (the original `except json.JSONDecodeError: continue`)
    deleted the questionnaire that NAMED the token while
    surveys/by-token/{token}.json survived, still resolving to this slug -- and
    with no reverse lookup from the index, every retry then answered 204 over
    something no code can ever reach again. A rebuild reusing the slug turns
    that stale token into a live credential into the NEW survey."""
    _seed_everything(proto_env)
    proto_env["s3"].blobs[f"prototypes/{SLUG}/survey/questionnaire.json"] = \
        "{truncated"

    resp = client.delete(f"/projects/{PID}/prototypes/{SLUG}")

    assert resp.status_code == 502
    assert "survey" in resp.json()["detail"]
    # Nothing later ran, so the token stays reclaimable rather than stranded.
    assert f"prototypes/{SLUG}/survey/questionnaire.json" in proto_env["s3"].blobs
    assert "surveys/by-token/tok-1.json" in proto_env["root_s3"].blobs


def test_reset_keeps_the_session_so_a_failed_close_can_be_retried(
        proto_env, monkeypatch):
    """The build slot must be reclaimable by pressing the button again.

    The session used to be POPPED before close() was attempted, so a failed
    close left no session for the retry to find -- it saw the normal "no
    session" case and answered 204 while the slot close() releases stayed held
    until the process restarted. Reproduced exactly: first DELETE 502 with the
    registry entry already gone, retry 204, close() never called a second time,
    active_builds stuck at 1. That contradicts this route's own docstring."""
    _seed_spec(proto_env["s3"])
    session = FakePrototypeSession()
    _install_session_factory(monkeypatch, session)
    client.post(f"/projects/{PID}/prototypes/{SLUG}/session")
    assert app_module.build_semaphore.snapshot()["active_builds"] == 1

    closes = {"n": 0}
    real_close = session.close

    async def fail_once_then_real():
        closes["n"] += 1
        if closes["n"] == 1:
            raise RuntimeError("builder wedged")
        await real_close()

    monkeypatch.setattr(session, "close", fail_once_then_real)

    assert client.delete(f"/projects/{PID}/prototypes/{SLUG}").status_code == 502
    # Still registered: that is the ONLY handle a retry has on the held slot.
    assert app_module.proto_sessions.get((PID, SLUG)) is session
    assert app_module.build_semaphore.snapshot()["active_builds"] == 1

    assert client.delete(f"/projects/{PID}/prototypes/{SLUG}").status_code == 204

    assert closes["n"] == 2  # the retry actually re-attempted close()
    assert (PID, SLUG) not in app_module.proto_sessions
    assert app_module.build_semaphore.snapshot()["active_builds"] == 0


# ---- path-traversal guard (router-wide) ----

#: Every spelling that actually ROUTES to a handler carrying a traversing slug.
#: All are percent-encoded on purpose: Starlette normalises dot segments in the
#: raw URL, so a LITERAL `..`/`.` never reaches these handlers at all (`..`
#: 404s at the router, `.` collapses the path onto a different route). The
#: encoded forms arrive already decoded in `path_params` and route fine --
#: verified directly: `DELETE /projects/me/prototypes/%2e%2e` reached the
#: handler with slug == "..". Hence the guard must sit after decoding, which is
#: also why these variants and not the bare ones are what the guard is tested
#: against. `%2e` is the "." family (collapses `root/pid/slug` onto
#: `root/pid` -- every SIBLING prototype); `%252e%252e` is double-encoded and
#: decoded once to "..". The empty-segment case is unroutable here and is
#: covered against the primitive in test_proto_host.py instead.
_TRAVERSAL_SLUGS = ["%2e%2e", "%2E%2E", "%252e%252e", "%2e"]


@pytest.mark.parametrize("bad", _TRAVERSAL_SLUGS)
def test_reset_refuses_a_traversing_slug_and_deletes_nothing(proto_env, bad):
    """The CRITICAL one: `ProtoHost.purge` computes `{root}/{pid}/{slug}` and
    rmtree's it, and `pathlib` does NOT normalise -- so slug ".." escapes to
    the root and wipes EVERY project's build tree while the route answers 204.
    Reproduced before the guard: purge("me", "..") emptied the whole root.

    The frontend cannot produce this (encodeURIComponent + Next's URL
    normalisation) and nginx normalises dot segments, but the backend is
    reachable directly on :8000 (the README dev command binds 0.0.0.0) and an
    unconfigured deployment treats every request as admin.
    """
    _seed_everything(proto_env, monkeypatch=None)
    victim = proto_env["root"] / "victim-project" / "victim-slug"
    victim.mkdir(parents=True)
    (victim / "package.json").write_text("{}", encoding="utf-8")

    resp = client.delete(f"/projects/{PID}/prototypes/{bad}")

    assert resp.status_code == 404
    # Nothing at all was purged: another project's tree, this project's own
    # tree, and the S3 state all survive.
    assert victim.is_dir()
    assert (proto_env["root"] / PID / SLUG).is_dir()
    assert f"prototypes/{SLUG}/session.json" in proto_env["s3"].blobs
    assert "surveys/by-token/tok-1.json" in proto_env["root_s3"].blobs


@pytest.mark.parametrize("bad", _TRAVERSAL_SLUGS)
def test_start_host_refuses_a_traversing_slug(proto_env, bad):
    """The guard is router-wide, not reset-only: `start_host` hands the same
    unvalidated value to `_prototype_dir` and would `npm install`/`npm run
    build` inside a directory the caller chose (`{root}/{pid}/..`)."""
    _seed_spec(proto_env["s3"])

    assert client.post(
        f"/projects/{PID}/prototypes/{bad}/host").status_code == 404

    assert proto_env["host"].start_cwds == []


@pytest.mark.parametrize("bad", _TRAVERSAL_SLUGS)
def test_archive_refuses_a_traversing_slug(proto_env, bad):
    """Reset is the destructive consumer, but not the only affected one -- which
    is why the guard sits on the router rather than inside reset_prototype.

    `_archive_entries` rglob's `{root}/{pid}/{slug}` and zips every file it
    finds, so slug ".." walks the whole proto root. Reproduced before the guard:
    `GET .../prototypes/%2e%2e/archive` returned 200 with a zip containing
    `victim/vslug/SECRET.txt` -- another project's source, handed to a caller
    authorised only for this one."""
    _seed_everything(proto_env)  # so {root}/{PID} exists for ".." to climb out of
    victim = proto_env["root"] / "victim-project" / "victim-slug"
    victim.mkdir(parents=True)
    (victim / "SECRET.txt").write_text("another project's source",
                                       encoding="utf-8")

    resp = client.get(f"/projects/{PID}/prototypes/{bad}/archive")

    assert resp.status_code == 404
    assert b"SECRET" not in resp.content


def test_reset_refuses_a_traversing_pid_before_attempting_any_purge(proto_env):
    """`{root}/{pid}/{slug}` has TWO attacker-supplied segments, and a guard on
    only the slug leaves `root / ".." / slug` reachable.

    The project has to be REGISTERED for this to reach past
    `_require_registered` -- and it can be: POST /projects validates nothing, so
    `{"project_id": ".."}` registers fine (verified). Without the router guard
    this is a 502 rather than a 404: the request gets all the way into
    `ProtoHost.purge`, whose own `reject_unsafe_segment` is the thing that
    stops it. That layering is deliberate, and this test pins the OUTER layer
    -- a 502 would mean the route reported a failed reset of a path it should
    never have addressed at all."""
    app_module.registry.register("..")
    try:
        resp = client.delete(f"/projects/%2e%2e/prototypes/{SLUG}")
    finally:
        app_module.registry.remove("..")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "invalid pid"


def test_an_ordinary_slug_still_passes_the_guard(proto_env):
    """The guard must not reject the legitimate shapes -- including a
    non-ASCII slug, which arrives percent-encoded and decoded just like the
    attacks above, and a name whose ".." is a substring rather than a
    segment."""
    for slug in ("todo-app", "한글-앱", "..foo"):
        proto_env["s3"].blobs[
            f"aiplc-docs/discovery/prototypes/{slug}/PROTOTYPE-{slug}.md"] = "# p"
        from urllib.parse import quote
        assert client.delete(
            f"/projects/{PID}/prototypes/{quote(slug)}").status_code == 204, slug


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


def test_host_start_base_path_is_the_browser_visible_prefix(proto_env, monkeypatch):
    """basePath is a BROWSER-side value, and in production the browser's path
    carries an `/api` mount that the backend never sees.

    The chain: browser requests /api/proto/{pid}/{slug}/... -> nginx -> Next's
    app/api/[...path]/route.ts, which strips `/api` before forwarding to
    FastAPI, so this app only ever observes /proto/{pid}/{slug}/....  Baking the
    backend-side prefix into the build would emit asset URLs at
    /proto/.../_next/static/... -- which CloudFront 404s, the very symptom this
    work is fixing.

    The mount is configuration, not a constant: AIPDS_PUBLIC_PATH_PREFIX
    carries it, defaulting to "/api" to match the deployed nginx/Next wiring.
    """
    _seed_spec(proto_env["s3"])
    proto_dir = proto_env["root"] / PID / SLUG / "prototype"
    proto_dir.mkdir(parents=True)
    (proto_dir / "package.json").write_text("{}", encoding="utf-8")

    assert client.post(f"/projects/{PID}/prototypes/{SLUG}/host").status_code == 200

    assert proto_env["host"].start_base_paths == [f"/api/proto/{PID}/{SLUG}"]


def test_host_start_base_path_honours_an_empty_public_prefix(proto_env, monkeypatch):
    """Local dev calls the backend directly (no /api mount), so the override
    must be able to clear the prefix entirely rather than only replace it."""
    monkeypatch.setenv("AIPDS_PUBLIC_PATH_PREFIX", "")
    _seed_spec(proto_env["s3"])
    proto_dir = proto_env["root"] / PID / SLUG / "prototype"
    proto_dir.mkdir(parents=True)
    (proto_dir / "package.json").write_text("{}", encoding="utf-8")

    assert client.post(f"/projects/{PID}/prototypes/{SLUG}/host").status_code == 200

    assert proto_env["host"].start_base_paths == [f"/proto/{PID}/{SLUG}"]


def test_host_start_passes_the_projects_model(proto_env, monkeypatch):
    """호스팅이 프로젝트 모델을 프로토타입 런타임으로 넘긴다.

    이것이 없으면 사용자가 프로젝트에서 고른 모델이 세 곳 중 두 곳
    (Discovery·빌드 에이전트)에만 적용되고, 빌드된 앱은 에이전트가 자기
    `.env.example`에 박아 둔 모델로 돈다. 출처가 app.project_model() 하나여야
    세 곳이 갈라지지 않는다."""
    import aipds.app as app_module
    monkeypatch.setattr(app_module, "project_model",
                        lambda pid: "global.anthropic.claude-opus-5")
    _seed_spec(proto_env["s3"])
    proto_dir = proto_env["root"] / PID / SLUG / "prototype"
    proto_dir.mkdir(parents=True)
    (proto_dir / "package.json").write_text("{}", encoding="utf-8")

    assert client.post(f"/projects/{PID}/prototypes/{SLUG}/host").status_code == 200

    assert proto_env["host"].start_model_ids == ["global.anthropic.claude-opus-5"]


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


# ---- hosting: brand refresh right before the rebuild ----
#
# 이 태스크가 성립시키는 속성: 이미 완료된 프로토타입이 개선 세션 없이
# **재호스팅만으로** 리브랜딩된다. 호스팅은 rmtree 없이 기존 트리에
# `npm run build`를 돌리므로(proto/host.py), 리빌드 직전에 테마 파일만 새로
# 쓰면 코드는 한 줄도 안 건드리고 색이 바뀐다.
#
# "빌드 도중 호스팅"이 이 규율을 깨지 않는 이유: 바로 위
# test_host_start_409_while_a_build_session_is_live가 증명하듯,
# `_live_session`(session.status가 _DEAD_STATUSES 밖이면 살아 있다고 본다)이
# 이미 모든 살아 있는 세션 -- starting/building/waiting_input/ready 전부 --
# 에서 호스팅 시작을 409로 막는다(routes/prototypes.py:590-593). 그래서 이
# 태스크는 "빌드 중에는 갱신을 건너뛴다"는 별도 조건이 필요 없다: 빌드 중인
# 프로토타입은 애초에 여기까지 오지 못한다.

def test_host_start_refreshes_the_brand_theme_before_building(proto_env, monkeypatch):
    """리빌드는 ProtoHost.start() 호출 **안에서** 일어난다. 그 호출이 끝난
    뒤에 파일을 읽으면 "리빌드 전에 이미 새 값이었다"와 "리빌드가 끝난 뒤에야
    바뀌었다"를 구별할 수 없으므로, FakeProtoHost.start를 감싸 호출 진입
    시점의 내용을 캡처한다."""
    import asyncio

    from aipds.design_profile import DesignProfileStore
    from aipds.proto.design_sync import THEME_FILENAME

    _seed_spec(proto_env["s3"])
    proto_dir = proto_env["root"] / PID / SLUG / "prototype"
    proto_dir.mkdir(parents=True)
    (proto_dir / "package.json").write_text("{}", encoding="utf-8")
    build_dir = proto_dir.parent
    (build_dir / THEME_FILENAME).write_text("/* 낡은 테마 */", encoding="utf-8")

    profiles = DesignProfileStore(FakeS3Store())
    asyncio.run(profiles.save(filename="acme.md", uploaded_by="admin@x",
                              markdown="```tokens\nprimary: #111111\n```\n"))
    monkeypatch.setattr(app_module, "design_profile_store", lambda: profiles)

    host = proto_env["host"]
    seen: dict = {}
    real_start = host.start

    async def capturing_start(pid, slug, cwd=None, base_path=None, model_id=None):
        # 리빌드는 이 호출 안에서 일어난다 -- 그 전에 파일이 새 값이어야 한다.
        seen["css"] = (build_dir / THEME_FILENAME).read_text()
        return await real_start(pid, slug, cwd=cwd, base_path=base_path,
                                model_id=model_id)

    monkeypatch.setattr(host, "start", capturing_start)

    resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/host")

    assert resp.status_code == 200
    assert "#111111" in seen["css"]


def test_host_start_succeeds_even_if_the_brand_sync_fails(proto_env, monkeypatch):
    """브랜드 반영 실패가 호스팅 자체를 막지 않는다 -- 화면이 열리는 것이
    색보다 우선이다. design_profile_store() 자체가 터지는 경우로 실측한다:
    브리프의 try/except가 그 호출까지 감싸므로, 여기서 실패해도 /host는
    평소처럼 200을 내야 한다."""
    _seed_spec(proto_env["s3"])
    proto_dir = proto_env["root"] / PID / SLUG / "prototype"
    proto_dir.mkdir(parents=True)
    (proto_dir / "package.json").write_text("{}", encoding="utf-8")

    def boom():
        raise RuntimeError("s3 unreachable")

    monkeypatch.setattr(app_module, "design_profile_store", boom)

    resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/host")

    assert resp.status_code == 200


def test_host_start_warns_when_no_theme_copy_exists_to_refresh(
        proto_env, monkeypatch, caplog):
    """sync_design은 갱신만 한다 -- 프로필 업로드 이전에 빌드된 프로토타입은
    prototype/ 아래에 테마 사본이 없어 재호스팅해도 무브랜드로 남는다. 그
    상황에서 아무 표시도 없으면 운영자는 왜 아무 일도 안 일어났는지 알 방법이
    없다. 그 트리를 흉내내려고(프로필 업로드 이전에 만들어진 빌드) 테마
    사본을 심지 않는다."""
    import asyncio

    from aipds.design_profile import DesignProfileStore

    _seed_spec(proto_env["s3"])
    proto_dir = proto_env["root"] / PID / SLUG / "prototype"
    proto_dir.mkdir(parents=True)
    (proto_dir / "package.json").write_text("{}", encoding="utf-8")
    # 일부러 aipds-theme.css 사본을 심지 않는다.

    profiles = DesignProfileStore(FakeS3Store())
    asyncio.run(profiles.save(filename="acme.md", uploaded_by="admin@x",
                              markdown="```tokens\nprimary: #111111\n```\n"))
    monkeypatch.setattr(app_module, "design_profile_store", lambda: profiles)

    with caplog.at_level("WARNING"):
        resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/host")

    assert resp.status_code == 200
    assert any("theme copy" in r.message for r in caplog.records)


def test_host_start_does_not_warn_when_a_theme_copy_exists(
        proto_env, monkeypatch, caplog):
    """반대 경우의 회귀 가드 -- 사본이 있으면(정상 경로) 경고가 뜨지 않는다."""
    import asyncio

    from aipds.design_profile import DesignProfileStore
    from aipds.proto.design_sync import THEME_FILENAME

    _seed_spec(proto_env["s3"])
    proto_dir = proto_env["root"] / PID / SLUG / "prototype"
    proto_dir.mkdir(parents=True)
    (proto_dir / "package.json").write_text("{}", encoding="utf-8")
    (proto_dir / THEME_FILENAME).write_text("/* stub */", encoding="utf-8")

    profiles = DesignProfileStore(FakeS3Store())
    asyncio.run(profiles.save(filename="acme.md", uploaded_by="admin@x",
                              markdown="```tokens\nprimary: #111111\n```\n"))
    monkeypatch.setattr(app_module, "design_profile_store", lambda: profiles)

    with caplog.at_level("WARNING"):
        resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/host")

    assert resp.status_code == 200
    assert not any("theme copy" in r.message for r in caplog.records)


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


def _authorize(host, pid=PID, slug=SLUG) -> dict[str, str]:
    """이 프로토타입의 접근 쿠키를 만들어 헤더 dict로 준다.

    아래 프록시 테스트들이 단정하는 것은 **경로 전달**(basePath 정합성)이고
    인증이 아니다. 그 테스트들이 쿠키를 들고 가지 않으면 전부 404에서 멈춰,
    프리픽스를 잘못 전달하는 회귀를 더 이상 잡지 못한다 — 통과는 하지만
    아무것도 검증하지 않는 상태가 된다.

    인증 자체는 test_routes_proto_public.py가 단정한다.
    """
    token = host.ensure_token(pid, slug)
    return {"Cookie": f"{cookie_name(pid, slug)}={token}"}


def test_proxy_forwards_the_prefix_intact(proto_env, echo_server):
    """The proxy must forward /proto/{pid}/{slug}/... UNCHANGED, not strip the
    prefix.

    This is what lets the prototype build with Next.js `basePath` set to that
    same prefix. basePath changes URL generation AND request matching together,
    so an app built with it expects to receive the prefixed path. Stripping it
    (the old behaviour) left two broken halves: without basePath the app's own
    asset URLs pointed at the CloudFront root (/_next/static/... -> 404), and
    with basePath the app 404'd every stripped request itself.

    Forwarding intact also covers what `assetPrefix` alone cannot: files served
    straight out of public/ (<img src="/logo.png">) and client-side router
    hrefs, neither of which assetPrefix rewrites.
    """
    proto_env["host"].infos[(PID, SLUG)] = HostInfo(
        state="running", port=echo_server, log_tail="")
    from aipds.routes.proto_public import public_base_path
    resp = client.get(f"/proto/{PID}/{SLUG}/some/page",
                      params={"q": "1"},
                      headers={"X-Origin-Verify": "secret-value",
                               **_authorize(proto_env["host"])})
    assert resp.status_code == 200
    assert resp.text == f"echo:{public_base_path(PID, SLUG)}/some/page?q=1"
    # The CloudFront shared secret must not reach the prototype process.
    forwarded = _EchoHandler.seen_headers[0]
    assert not any(k.lower() == "x-origin-verify" for k in forwarded)


def test_proxy_forwards_the_same_prefix_the_build_was_given(proto_env, echo_server):
    """The two halves must agree: whatever prefix is baked into the build
    (`public_base_path`) is what the app matches on, so that is what the proxy
    has to forward.

    They are NOT the same string as the one this app routes on -- the browser's
    path carries an `/api` mount that Next strips before FastAPI sees it. So the
    proxy has to put the full browser-side prefix BACK before forwarding, or the
    app (built with basePath=/api/proto/...) 404s a request for /proto/....
    Pinning the pairing, not the literals, is the point: this is exactly the
    kind of two-places-derive-it-separately gap that produced the original
    404s."""
    from aipds.routes.proto_public import public_base_path
    proto_env["host"].infos[(PID, SLUG)] = HostInfo(
        state="running", port=echo_server, log_tail="")

    resp = client.get(f"/proto/{PID}/{SLUG}/_next/static/chunks/main.js",
                      headers=_authorize(proto_env["host"]))

    assert resp.status_code == 200
    assert resp.text == \
        f"echo:{public_base_path(PID, SLUG)}/_next/static/chunks/main.js"


def test_proxy_forwards_a_public_dir_asset_under_the_prefix(proto_env, echo_server):
    """Covers what `assetPrefix` alone would have missed: a file served straight
    out of public/ (<img src="/logo.png">) carries the basePath too, so it also
    has to arrive prefixed."""
    from aipds.routes.proto_public import public_base_path
    proto_env["host"].infos[(PID, SLUG)] = HostInfo(
        state="running", port=echo_server, log_tail="")
    resp = client.get(f"/proto/{PID}/{SLUG}/logo.png",
                      headers=_authorize(proto_env["host"]))
    assert resp.status_code == 200
    assert resp.text == f"echo:{public_base_path(PID, SLUG)}/logo.png"


def test_proxy_502_when_not_running(proto_env):
    """호스팅이 꺼져 있을 때의 502는 **인증된** 요청에만 보인다.

    쿠키를 들고 가는 것이 이 테스트의 핵심이다: 쿠키가 없으면 404가 먼저
    나가므로(그것이 의도다 — 존재를 숨긴다) 이 502 분기는 도달하지 않는다.
    """
    resp = client.get(f"/proto/{PID}/{SLUG}/index.html",
                      headers=_authorize(proto_env["host"]))
    assert resp.status_code == 502
    assert "start hosting first" in resp.text


def test_failed_session_does_not_wedge_prototype(proto_env, monkeypatch):
    """A failed session must neither block a restart (409) nor be served as a
    live stream (404): that combination wedged the prototype permanently —
    POST said 'already active' while GET said 'no active session'."""
    dead = FakePrototypeSession()
    dead.status = "failed"
    app_module.proto_sessions[(PID, SLUG)] = dead

    # A turn must not be started off a dead session.
    assert client.post(f"/projects/{PID}/prototypes/{SLUG}/turns",
                       json={"text": "hi"}).status_code == 404

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
    resp = client.get(f"/proto/{PID}/{SLUG}", follow_redirects=False,
                      headers=_authorize(proto_env["host"]))
    assert resp.status_code == 307
    location = resp.headers["location"]
    assert location == f"/proto/{PID}/{SLUG}/"
    assert "://" not in location  # never absolute — must stay on the public host


def test_proxy_root_redirect_preserves_query(proto_env, echo_server):
    proto_env["host"].infos[(PID, SLUG)] = HostInfo(
        state="running", port=echo_server, log_tail="")
    resp = client.get(f"/proto/{PID}/{SLUG}?a=1&b=2", follow_redirects=False,
                      headers=_authorize(proto_env["host"]))
    assert resp.headers["location"] == f"/proto/{PID}/{SLUG}/?a=1&b=2"


def test_proxy_relative_asset_under_slug_prefix_is_served(proto_env, echo_server):
    """The asset path a browser derives from the slash form
    (.../{slug}/styles.css) must reach the prototype — these were the 502s."""
    proto_env["host"].infos[(PID, SLUG)] = HostInfo(
        state="running", port=echo_server, log_tail="")
    from aipds.routes.proto_public import public_base_path
    resp = client.get(f"/proto/{PID}/{SLUG}/styles.css",
                      headers=_authorize(proto_env["host"]))
    assert resp.status_code == 200
    assert resp.text == f"echo:{public_base_path(PID, SLUG)}/styles.css"


def test_proxy_rewrites_upstream_absolute_redirect(proto_env):
    """A prototype redirecting to its own internal origin must be rewritten to
    the public proxy path — otherwise the browser chases 127.0.0.1:<port>."""
    from aipds.routes.proto_public import (_rewritten_location,
                                                public_base_path)
    got = _rewritten_location(
        "http://127.0.0.1:4001/login?next=/dash", PID, SLUG)
    # Browser-side prefix (includes the `/api` mount) -- a Location header is
    # resolved by the browser, not by this app.
    assert got == f"{public_base_path(PID, SLUG)}/login?next=/dash"


def test_proxy_rewrites_upstream_relative_root_redirect(proto_env):
    from aipds.routes.proto_public import (_rewritten_location,
                                                public_base_path)
    assert _rewritten_location("/dashboard", PID, SLUG) == \
        f"{public_base_path(PID, SLUG)}/dashboard"


def test_proxy_does_not_double_prefix_an_already_prefixed_redirect(proto_env):
    """New hazard introduced by forwarding the prefix intact: an app built with
    `basePath` emits redirects that ALREADY carry the prefix. Prepending it
    again would send the browser to /proto/{pid}/{slug}/proto/{pid}/{slug}/...

    Covers both shapes a prototype can produce -- a bare path and an absolute
    URL naming its own internal origin."""
    from aipds.routes.proto_public import (_rewritten_location,
                                                public_base_path)
    # The app was built with basePath = public_base_path, so THAT is the prefix
    # its own redirects carry -- including the `/api` mount this app never sees
    # on inbound requests.
    prefix = public_base_path(PID, SLUG)

    assert _rewritten_location(f"{prefix}/dashboard", PID, SLUG) == \
        f"{prefix}/dashboard"
    assert _rewritten_location(
        f"http://127.0.0.1:4001{prefix}/login?next=/dash", PID, SLUG) == \
        f"{prefix}/login?next=/dash"
    # The prefix itself, with and without a trailing slash.
    assert _rewritten_location(prefix, PID, SLUG) == prefix
    assert _rewritten_location(f"{prefix}/", PID, SLUG) == f"{prefix}/"


def test_proxy_prefixes_a_sibling_path_that_merely_shares_a_leading_segment(proto_env):
    """The double-prefix guard must match on a path SEGMENT boundary, not a
    string prefix: /proto/{pid}/{slug}-other belongs to a different prototype
    and still needs the prefix prepended (a plain startswith would skip it)."""
    from aipds.routes.proto_public import (_rewritten_location,
                                                public_base_path)
    prefix = public_base_path(PID, SLUG)
    assert _rewritten_location(f"{prefix}-other/page", PID, SLUG) == \
        f"{prefix}{prefix}-other/page"


def test_proxy_leaves_external_redirect_alone(proto_env):
    from aipds.routes.proto_public import _rewritten_location
    ext = "https://accounts.google.com/o/oauth2/auth?x=1"
    assert _rewritten_location(ext, PID, SLUG) == ext


# ---- 완료된 세션은 죽은 세션이다 ----

def test_a_completed_session_does_not_block_hosting(proto_env, monkeypatch):
    """이 작업의 동기가 된 결함: 빌드가 끝나도 세션이 살아 있으면 [호스팅
    시작]이 409로 막혔다. 카드는 이미 '빌드 완료 / 호스팅 시작'을 보여준다."""
    session = FakePrototypeSession()
    session.status = "complete"
    app_module.proto_sessions[(PID, SLUG)] = session

    resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/host")

    assert resp.status_code == 200
    assert resp.json()["state"] == "running"


def test_a_completed_session_does_not_block_a_new_start(proto_env, monkeypatch):
    """'개선 이어서 하기'가 필요로 하는 것."""
    old = FakePrototypeSession()
    old.status = "complete"
    app_module.proto_sessions[(PID, SLUG)] = old
    app_module.s3_store_factory(PID).blobs[SPEC_KEY] = "# spec"

    resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/session")

    assert resp.status_code == 202


def test_a_completed_session_serves_no_stream(proto_env):
    """답할 future가 없는 세션에 스트림을 열어주면 안 된다."""
    session = FakePrototypeSession()
    session.status = "complete"
    app_module.proto_sessions[(PID, SLUG)] = session

    resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/turns", json={"text": "hi"})

    assert resp.status_code == 404


def test_answers_on_a_completed_session_404(proto_env):
    session = FakePrototypeSession()
    session.status = "complete"
    app_module.proto_sessions[(PID, SLUG)] = session

    resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/answers",
                       json={"answers": {"1": "A"}})

    assert resp.status_code == 404


def test_list_state_built_for_a_completed_session(proto_env, monkeypatch):
    """complete는 _WORKING_STATUSES에 없으므로 카드가 '빌드 중'에 고정되지
    않는다."""
    s3 = app_module.s3_store_factory(PID)
    s3.blobs[SPEC_KEY] = "# spec"
    session = FakePrototypeSession()
    session.status = "complete"
    app_module.proto_sessions[(PID, SLUG)] = session
    proto_dir = app_module._proto_root() / PID / SLUG / "prototype"
    proto_dir.mkdir(parents=True, exist_ok=True)
    (proto_dir / "index.html").write_text("x")

    resp = client.get(f"/projects/{PID}/prototypes")

    entry = next(p for p in resp.json()["prototypes"] if p["slug"] == SLUG)
    assert entry["state"] == "built"


# ---- 긴 입력을 URL에서 빼는 2단계 핸들 (HTTP 431 결함) ----
#
# 워크스페이스 채팅과 같은 결함이다(frontend lib/api/sse.ts의 createTurn에 실측):
# 긴 한글 입력이 URL에 실리면 요청 라인이 커져 프록시가 431을 내고,
# EventSource는 상태 코드를 노출하지 않아 "연결이 끊어졌습니다"만 보인다.


def test_session_turn_handle_carries_text_out_of_the_url(proto_env, monkeypatch):
    _seed_spec(proto_env["s3"])
    session = FakePrototypeSession()
    _install_session_factory(monkeypatch, session)
    client.post(f"/projects/{PID}/prototypes/{SLUG}/session")
    long_text = "가" * 3000
    r = client.post(f"/projects/{PID}/prototypes/{SLUG}/turns",
                    json={"text": long_text})
    assert r.status_code == 200
    handle = r.json()["turn_id"]
    # 핸들이 URL에 들어가므로 짧아야 한다 — 이것이 이 설계의 핵심이다.
    assert len(handle) <= 64
    with client.stream("GET", f"/projects/{PID}/prototypes/{SLUG}/events",
                       params={"turn": handle}) as resp:
        list(resp.iter_lines())
    # 에이전트는 원문 전체를 받았다 — 핸들이 텍스트를 잘라먹지 않는다.
    # 첫 메시지이므로 감싸진다 — 이 테스트가 지키는 것은 긴 텍스트가 URL이
    # 아니라 핸들로 왔다는 것이다.
    assert session.messages == [f"FIRST({long_text})"]


def _data(lines):
    return [json.loads(l[len("data:"):].strip()) for l in lines
            if l.startswith("data:")]


def test_a_build_turn_can_be_watched_again(proto_env, monkeypatch):
    """턴 id는 1회용 핸들이 아니라 **다시 보는** 이름이다 — 빌드 화면이 새로고침돼도
    같은 턴을 처음부터(`after=0`) 또는 마지막으로 받은 곳부터 본다."""
    _seed_spec(proto_env["s3"])
    _install_session_factory(monkeypatch, FakePrototypeSession())
    client.post(f"/projects/{PID}/prototypes/{SLUG}/session")
    turn = client.post(f"/projects/{PID}/prototypes/{SLUG}/turns",
                       json={"text": "고쳐 줘"}).json()["turn_id"]
    url = f"/projects/{PID}/prototypes/{SLUG}/events"
    with client.stream("GET", url, params={"turn": turn}) as r:
        first = _data(list(r.iter_lines()))
    with client.stream("GET", url, params={"turn": turn}) as r:
        again = _data(list(r.iter_lines()))
    assert first == again and first[-1]["kind"] == "done"
    with client.stream("GET", url, params={"turn": turn, "after": 1}) as r:
        assert [e["kind"] for e in _data(list(r.iter_lines()))] == ["done"]
    assert client.get(url, params={"turn": "nope"}).status_code == 404


def test_the_session_lists_its_turns_for_a_reopened_panel(proto_env, monkeypatch):
    """다시 열린 빌드 화면이 대화를 되살리는 근거다: 이 세션의 턴들과 각 턴에서
    사용자가 한 말(자동 개시는 None)."""
    _seed_spec(proto_env["s3"])
    fake = FakePrototypeSession()
    _install_session_factory(monkeypatch, fake)
    client.post(f"/projects/{PID}/prototypes/{SLUG}/session")
    base = f"/projects/{PID}/prototypes/{SLUG}"
    first = client.post(f"{base}/turns", json={"text": "__first__"}).json()["turn_id"]
    _wait_until(lambda: client.get(f"{base}/session").json()["turns"][0]["state"] == "done")
    second = client.post(f"{base}/turns", json={"text": "버튼 색을 바꿔 줘"}).json()["turn_id"]
    _wait_until(lambda: client.get(f"{base}/session").json()["turns"][-1]["state"] == "done")
    body = client.get(f"{base}/session").json()
    assert [(t["turn_id"], t["input"]) for t in body["turns"]] == [
        (first, None), (second, "버튼 색을 바꿔 줘")]
    assert client.get(f"/projects/{PID}/prototypes/other/session").status_code == 404


def test_a_second_build_turn_while_one_runs_is_409_with_its_id(proto_env, monkeypatch):
    _seed_spec(proto_env["s3"])
    gate = {}

    class Gated(FakePrototypeSession):
        async def send_message(self, text):
            self.messages.append(text)
            gate["event"] = asyncio.Event()
            yield AgentEvent(kind="message", text="building")
            await gate["event"].wait()
            yield AgentEvent(kind="done")

    fake = Gated()
    _install_session_factory(monkeypatch, fake)
    base = f"/projects/{PID}/prototypes/{SLUG}"
    client.post(f"{base}/session")
    turn = client.post(f"{base}/turns", json={"text": "__first__"}).json()["turn_id"]
    r = client.post(f"{base}/turns", json={"text": "또"})
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "turn_in_progress", "turn_id": turn}
    _wait_until(lambda: "event" in gate)
    client.portal.call(_release, gate["event"])
    _wait_until(lambda: client.get(f"{base}/session").json()["turns"][0]["state"] == "done")


async def _release(event):
    event.set()


def _wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached")


def test_events_only_watches_a_build_turn(proto_env, monkeypatch):
    """`/events`는 턴을 **보기만** 한다 — 턴은 `POST …/turns`가 시작한다."""
    _seed_spec(proto_env["s3"])
    _install_session_factory(monkeypatch, FakePrototypeSession())
    client.post(f"/projects/{PID}/prototypes/{SLUG}/session")
    url = f"/projects/{PID}/prototypes/{SLUG}/events"
    assert client.get(url).status_code == 422
    assert client.get(url, params={"text": "__first__"}).status_code == 422


def test_list_ignores_other_files_in_the_single_prototype_dir(proto_env):
    """그 디렉터리에는 design-context·build-instructions 등이 함께 쌓인다
    (prototype-validation.md의 산출물 7개). 명세만 카드가 돼야 한다."""
    proto_env["s3"].blobs["aiplc-docs/discovery/prototype/design-context.md"] = "x"
    proto_env["s3"].blobs["aiplc-docs/discovery/prototype/build-instructions.md"] = "y"
    body = client.get(f"/projects/{PID}/prototypes").json()
    assert body["prototypes"] == []


# ---- 사용자의 첫 메시지가 개시 턴이 된다 ----
#
# "수정하기"를 누른 사람은 이미 무엇을 고칠지 알고 있는데, 개시 프롬프트가 되묻고
# 그 질문이 떠 있는 동안 입력창이 비활성이라 자기 요청을 타이핑할 수조차 없었다
# (근거는 aipds/proto/prompts.handoff_prompt).
#
# 판정 기준이 `session.opened`인 것이 요점이다 — UI 플래그가 아니다. "개시 턴이
# 아직 안 돌았다"는 세션이 아는 사실이고, 프론트가 추측하면 새로고침·경합·이미
# 열린 세션에서 어긋난다.

def test_the_first_user_message_is_wrapped_as_the_opening_turn(proto_env, monkeypatch):
    _seed_spec(proto_env["s3"])
    session = FakePrototypeSession()
    _install_session_factory(monkeypatch, session)
    client.post(f"/projects/{PID}/prototypes/{SLUG}/session")

    _build("장바구니 버튼을 오른쪽 위로")

    # 세션이 받은 것은 사용자의 생 문장이 아니라 그 요청이 실린 개시 프롬프트다.
    assert session.messages == ["FIRST(장바구니 버튼을 오른쪽 위로)"]


def test_a_later_message_is_an_ordinary_turn(proto_env, monkeypatch):
    """개시 턴이 한 번 돌면 그 뒤 메시지는 감싸지 않는다 — 감싸면 매 턴이
    "먼저 prototype/을 봐라"로 다시 시작한다."""
    _seed_spec(proto_env["s3"])
    session = FakePrototypeSession()
    _install_session_factory(monkeypatch, session)
    client.post(f"/projects/{PID}/prototypes/{SLUG}/session")

    for text in ("첫 요청", "두 번째 요청"):
        _build(text)

    assert session.messages == ["FIRST(첫 요청)", "두 번째 요청"]


def test_the_sentinel_still_opens_without_a_request(proto_env, monkeypatch):
    """자동 개시에는 사용자가 아무 말도 하지 않았다 — 그때는 되묻는 개시
    프롬프트가 맞는 동작이고, 이 변경이 그것을 없애서는 안 된다."""
    _seed_spec(proto_env["s3"])
    session = FakePrototypeSession()
    _install_session_factory(monkeypatch, session)
    client.post(f"/projects/{PID}/prototypes/{SLUG}/session")

    _build("__first__")

    assert session.messages == ["FIRST(None)"]


def test_a_long_request_rides_the_turn_handle(proto_env, monkeypatch):
    """긴 요청은 URL에 실을 수 없다(프록시 431 — frontend lib/api/sse.ts의 createTurn). 핸들
    경로도 개시 턴 판정을 똑같이 타야 한다."""
    _seed_spec(proto_env["s3"])
    session = FakePrototypeSession()
    _install_session_factory(monkeypatch, session)
    client.post(f"/projects/{PID}/prototypes/{SLUG}/session")

    long_text = "이 화면을 " + "아주 " * 500 + "크게"
    turn = client.post(f"/projects/{PID}/prototypes/{SLUG}/turns",
                       json={"text": long_text}).json()["turn_id"]
    with client.stream("GET", f"/projects/{PID}/prototypes/{SLUG}/events"
                              f"?turn={turn}") as resp:
        list(resp.iter_lines())

    assert session.messages == [f"FIRST({long_text})"]
