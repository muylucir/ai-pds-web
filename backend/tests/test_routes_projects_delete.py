# backend/tests/test_routes_projects_delete.py
import json

import pytest
from fastapi.testclient import TestClient
from aipds import app as app_module
from aipds.workspace import Workspace
from tests.fakes.in_memory_s3 import FakeS3Store

client = TestClient(app_module.app)


class _FakeRunner:
    def __init__(self):
        self.stopped = 0

    async def stop(self):
        self.stopped += 1


class _FakeHost:
    """프로토타입 실체(로컬 트리·호스팅·토큰)의 대역. `purge`는 실제로 트리를
    지운다 — 인메모리 카운터만 두면 "빌드 트리가 남는다"를 단정하는 테스트가
    아무것도 검증하지 않는다."""

    def __init__(self, root=None):
        self.root = root
        self.purged: list[tuple[str, str]] = []
        self.purge_exc: Exception | None = None
        self.purged_projects: list[str] = []
        self.purge_project_exc: Exception | None = None

    def slugs(self, pid):
        found: set[str] = set()
        if self.root is not None:
            base = self.root / pid
            if base.is_dir():
                found |= {c.name for c in base.iterdir() if c.is_dir()}
        return sorted(found)

    async def purge(self, pid, slug):
        self.purged.append((pid, slug))
        if self.purge_exc is not None:
            raise self.purge_exc
        if self.root is not None:
            import shutil
            shutil.rmtree(self.root / pid / slug, ignore_errors=True)

    async def purge_project(self, pid):
        """부모 디렉터리 정리. 실물처럼 **실제로** 지운다 — 카운터만 두면
        "빈 껍데기가 남지 않는다"를 단정하는 테스트가 공허해진다(`purge`와 같은
        이유)."""
        self.purged_projects.append(pid)
        if self.purge_project_exc is not None:
            raise self.purge_project_exc
        if self.root is not None:
            import shutil
            shutil.rmtree(self.root / pid, ignore_errors=True)


class _FakeSession:
    def __init__(self):
        self.closed = 0

    async def close(self):
        self.closed += 1


@pytest.fixture(autouse=True)
def _proto_wiring(monkeypatch, tmp_path):
    """삭제 경로가 프로토타입 정리를 타므로 그 의존성도 전부 fake로 물린다.

    s3_store_factory(프로젝트 프리픽스)와 surveys_root_s3_factory(버킷 루트)를
    **따로** 두는 것이 load-bearing이다 — 설문 토큰 인덱스는 루트에 있고, 둘을
    한 fake로 합치면 "인덱스가 회수됐다"는 단정이 공허해진다
    (test_routes_prototypes.py의 proto_env와 같은 이유).
    """
    project_s3, surveys_root = FakeS3Store(), FakeS3Store()
    host = _FakeHost(root=tmp_path)
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: project_s3)
    monkeypatch.setattr(app_module, "surveys_root_s3_factory", lambda: surveys_root)
    monkeypatch.setattr(app_module, "proto_host", lambda: host)
    sessions_backup = dict(app_module.proto_sessions)
    app_module.proto_sessions.clear()
    yield {"project_s3": project_s3, "surveys_root": surveys_root,
           "host": host, "proto_root": tmp_path}
    app_module.proto_sessions.clear()
    app_module.proto_sessions.update(sessions_backup)


def _seed_project(pid: str, sessions: FakeS3Store, root: FakeS3Store) -> _FakeRunner:
    runner = _FakeRunner()
    app_module.registry.register(pid)
    app_module.registry.attach(pid, Workspace(runner))
    sessions.blobs[f"session_{pid}/agents/agent_default/messages/message_0.json"] = "{}"
    root.blobs[f"{pid}/project.json"] = "{}"
    root.blobs[f"{pid}/aiplc-docs/audit.md"] = "x"
    return runner


def _seed_prototype(env, pid: str, slug: str, token: str) -> _FakeSession:
    """스펙만 있는 프로토타입이 아니라 **실체가 있는** 프로토타입을 만든다:
    S3 설문(문항 + 루트 토큰 인덱스), 로컬 빌드 트리 + 토큰 파일, 활성 세션."""
    env["project_s3"].blobs[f"prototypes/{slug}/survey/questionnaire.json"] = json.dumps(
        {"token": token, "slug": slug, "project_id": pid})
    env["surveys_root"].blobs[f"surveys/by-token/{token}.json"] = json.dumps(
        {"project_id": pid, "slug": slug})
    tree = env["proto_root"] / pid / slug / "prototype"
    tree.mkdir(parents=True)
    (tree / "package.json").write_text("{}", encoding="utf-8")
    (env["proto_root"] / pid / slug / ".proto-token").write_text(token, encoding="utf-8")
    session = _FakeSession()
    app_module.proto_sessions[(pid, slug)] = session
    return session


def test_delete_removes_registry_vm_and_s3(monkeypatch):
    sessions, root = FakeS3Store(), FakeS3Store()
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    runner = _seed_project("del-1", sessions, root)

    r = client.delete("/projects/del-1")
    assert r.status_code == 200 and r.json() == {"deleted": True}
    assert runner.stopped == 1
    assert not any(k.startswith("session_del-1/") for k in sessions.blobs)
    assert not any(k.startswith("del-1/") for k in root.blobs)
    assert not app_module.registry.is_registered("del-1")


def test_delete_purges_prototype_runtime_state_and_root_token_index(monkeypatch, _proto_wiring):
    """프로젝트 삭제가 프로토타입의 **실체**까지 정리한다.

    S3 projects/{pid}/ 삭제로는 닿지 않는 것들이다: 로컬 빌드 트리, 접근 토큰,
    활성 빌드 세션, 그리고 버킷 **루트**의 설문 토큰 인덱스. 이것들이 남으면
    수백MB 트리가 EBS에 쌓이고, 이미 공유한 프리뷰·설문 링크가 삭제된
    프로젝트에서도 계속 열린다.
    """
    env = _proto_wiring
    sessions, root = FakeS3Store(), FakeS3Store()
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    pid = "del-proto"
    _seed_project(pid, sessions, root)
    session = _seed_prototype(env, pid, "checkout", "tok-checkout")

    r = client.delete(f"/projects/{pid}")

    assert r.status_code == 200
    assert session.closed == 1
    assert (pid, "checkout") not in app_module.proto_sessions
    assert env["host"].purged == [(pid, "checkout")]
    assert not (env["proto_root"] / pid / "checkout").exists()
    # 루트 스코프 인덱스 — 역방향 조회가 없어서 문항 파일을 먼저 읽어야 회수된다.
    assert "surveys/by-token/tok-checkout.json" not in env["surveys_root"].blobs
    assert not app_module.registry.is_registered(pid)


def test_delete_cleans_every_slug_of_the_project(monkeypatch, _proto_wiring):
    """슬러그가 여럿이면 전부 돈다. 하나만 돌면 나머지는 조용히 남는다."""
    env = _proto_wiring
    sessions, root = FakeS3Store(), FakeS3Store()
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    pid = "del-multi"
    _seed_project(pid, sessions, root)
    _seed_prototype(env, pid, "alpha", "tok-a")
    _seed_prototype(env, pid, "beta", "tok-b")
    # 기록 없이 로컬 트리만 있는 슬러그(리셋 뒤 재빌드 등) — 로컬 열거가 없으면 놓친다.
    (env["proto_root"] / pid / "gamma").mkdir(parents=True)

    r = client.delete(f"/projects/{pid}")

    assert r.status_code == 200
    assert env["host"].purged == [(pid, "alpha"), (pid, "beta"), (pid, "gamma")]
    assert list(env["surveys_root"].blobs) == []          # 두 토큰 인덱스 모두 회수
    assert not (env["proto_root"] / pid / "gamma").exists()


def test_delete_500_keeps_s3_and_registry_when_build_tree_purge_fails(monkeypatch, _proto_wiring):
    """정리 실패는 조용히 넘기지 않는다 — 500 + 재시도 가능 상태 유지.

    S3를 지워버리면 남은 토큰 인덱스를 회수할 문항 파일이 사라져 그 인덱스는
    영구히 도달 불가가 된다. 그래서 실패 시 S3와 레지스트리를 그대로 둔다.
    """
    env = _proto_wiring
    sessions, root = FakeS3Store(), FakeS3Store()
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    pid = "del-stuck"
    _seed_project(pid, sessions, root)
    _seed_prototype(env, pid, "checkout", "tok-stuck")
    env["host"].purge_exc = RuntimeError("purge left residue")

    r = client.delete(f"/projects/{pid}")

    assert r.status_code == 500
    assert app_module.registry.is_registered(pid)
    assert f"{pid}/project.json" in root.blobs
    assert any(k.startswith(f"session_{pid}/") for k in sessions.blobs)


def test_delete_keeps_build_tree_when_survey_purge_fails(monkeypatch, _proto_wiring):
    """설문 purge가 실패한 슬러그는 빌드 트리도 건드리지 않는다.

    리셋 경로의 게이트와 같은 판단이다 — 설문(과 살아 있는 공개 링크)이 남은
    채 실체만 지우면 재시도가 회수할 문항은 그대로인데 호스팅할 것이 없다.
    """
    env = _proto_wiring
    sessions, root = FakeS3Store(), FakeS3Store()
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)

    class _ExplodingSurveyStore:
        def __init__(self, pid, slug):
            pass

        async def purge(self):
            raise RuntimeError("survey s3 down")

    monkeypatch.setattr(app_module, "survey_store_factory",
                        lambda pid, slug: _ExplodingSurveyStore(pid, slug))
    pid = "del-survey-fail"
    _seed_project(pid, sessions, root)
    _seed_prototype(env, pid, "checkout", "tok-keep")

    r = client.delete(f"/projects/{pid}")

    assert r.status_code == 500
    assert env["host"].purged == []                      # 이 슬러그는 더 진행하지 않는다
    assert (env["proto_root"] / pid / "checkout").is_dir()
    assert "surveys/by-token/tok-keep.json" in env["surveys_root"].blobs
    assert app_module.registry.is_registered(pid)


def test_delete_cleans_local_prototypes_without_durable_storage(monkeypatch, _proto_wiring):
    """버킷 미설정(로컬/테스트)에서도 로컬 실체 정리는 돈다 — 빌드 트리와 세션은
    S3와 무관하게 존재한다. 설문 단계만 건너뛴다.

    setenv("")가 필요하다: aipds.app이 기동 시 backend/.env를 로드하므로
    개발 박스에서는 PATHFINDER_S3_BUCKET이 이미 채워져 있고, 그러면 이 테스트가
    durable 경로를 타서 검증하려던 분기를 지나친다(실측 — 이 줄이 없어 설문
    인덱스가 지워졌다). test_routes_prototypes.py의 proto_env도 같은 이유로
    같은 줄을 둔다.
    """
    env = _proto_wiring
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "")
    pid = "del-nodurable"
    app_module.registry.register(pid)
    session = _seed_prototype(env, pid, "checkout", "tok-nd")

    r = client.delete(f"/projects/{pid}")

    assert r.status_code == 200
    assert session.closed == 1
    assert env["host"].purged == [(pid, "checkout")]
    assert not (env["proto_root"] / pid / "checkout").exists()
    # durable이 아니므로 설문 인덱스는 손대지 않는다(그 프로젝트는 S3에 없다).
    assert "surveys/by-token/tok-nd.json" in env["surveys_root"].blobs
    assert not app_module.registry.is_registered(pid)


def test_delete_unknown_project_404():
    assert client.delete("/projects/no-such").status_code == 404


def test_delete_continues_when_stop_fails(monkeypatch):
    sessions, root = FakeS3Store(), FakeS3Store()
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    runner = _seed_project("del-2", sessions, root)

    async def _boom():
        raise RuntimeError("runner stuck")

    runner.stop = _boom  # type: ignore[assignment]
    r = client.delete("/projects/del-2")
    assert r.status_code == 200  # stop 실패는 삭제를 막지 않는다
    assert not app_module.registry.is_registered("del-2")


def test_delete_returns_500_and_keeps_registry_on_s3_failure(monkeypatch):
    class _ExplodingStore(FakeS3Store):
        async def delete_prefix(self, prefix):
            raise RuntimeError("s3 down")

    sessions, root = _ExplodingStore(), FakeS3Store()
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    _seed_project("del-3", sessions, root)

    r = client.delete("/projects/del-3")
    assert r.status_code == 500
    assert app_module.registry.is_registered("del-3")  # 유지 → 재시도 가능


def test_delete_registered_but_unbooted_project(monkeypatch):
    # 복원 직후(워크스페이스 없음) 상태에서도 삭제 가능해야 한다
    sessions, root = FakeS3Store(), FakeS3Store()
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    app_module.registry.register("del-4")
    root.blobs["del-4/project.json"] = "{}"

    r = client.delete("/projects/del-4")
    assert r.status_code == 200
    assert not app_module.registry.is_registered("del-4")


def test_delete_stops_runner_attached_by_concurrent_boot_during_s3_await(monkeypatch):
    """역방향 레이스: DELETE가 복원-미부팅(워크스페이스 없음) 프로젝트에 대해 실행되어
    stop 블록을 건너뛴 뒤, S3 삭제 await 도중 동시 ensure_workspace가 초기화를
    마치고 registry.attach로 살아있는 워크스페이스를 붙인다. 마지막
    registry.remove(pid)가 반환하는 그 워크스페이스를 stop하지 않으면 방금 만든
    러너가 새어나간다(leak)."""
    pid = "del-race"
    runner2 = _FakeRunner()

    class _RaceSessionsStore(FakeS3Store):
        async def delete_prefix(self, prefix):
            # 아직 등록 상태(is_registered)인 동안 동시 부팅이 attach하는 순간을 흉내낸다.
            app_module.registry.attach(pid, Workspace(runner2))
            return await super().delete_prefix(prefix)

    sessions, root = _RaceSessionsStore(), FakeS3Store()
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    app_module.registry.register(pid)
    root.blobs[f"{pid}/project.json"] = "{}"

    r = client.delete(f"/projects/{pid}")
    assert r.status_code == 200
    assert runner2.stopped == 1
    assert not app_module.registry.is_registered(pid)


# ---- 로컬 실체의 잔여물 ----
#
# 실측(2026-08-19, 배포 인스턴스): `/opt/pathfinder/workspaces/`에 삭제된 프로젝트
# 6개의 디렉터리가 남아 있었고(합 2.35MB), S3 `projects/`에는 그중 어느 것도 없었다.
# `/opt/pathfinder/protos/`에는 빈 부모 디렉터리 3개가 남아 있었다.

def test_delete_removes_the_workspace_dir_of_a_project_never_booted_this_process(
        monkeypatch, tmp_path):
    """**재시작 뒤 한 번도 열지 않은 프로젝트**를 삭제해도 워크스페이스가 사라져야
    한다.

    이것이 흔한 경로다. 기동 시 `app.py`는 S3 매니페스트에서 목록만 복원하며
    `registry.register()`만 부르고 `attach()`는 하지 않는다(워크스페이스는 첫
    요청에서 lazy 초기화). 그래서 재시작 직후 모든 프로젝트가
    `has_workspace() == False`이고, 옛 삭제 경로는 로컬 정리를 그 플래그에 걸어
    뒀으므로 — `runner.stop()` 안에만 rmtree가 있었다 — 디스크의 디렉터리가 그대로
    남았다. 사용자에게는 "채팅 기록·문서가 영구 삭제된다"고 약속한 상태다.
    """
    sessions, root = FakeS3Store(), FakeS3Store()
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    ws_root = tmp_path / "workspaces"
    monkeypatch.setattr(app_module, "_workspaces_dir", lambda: ws_root)

    # 등록만 한다 — attach 없음. 재시작 직후의 상태다.
    app_module.registry.register("del-ws")
    root.blobs["del-ws/project.json"] = "{}"
    left_behind = ws_root / "del-ws" / "aiplc-docs"
    left_behind.mkdir(parents=True)
    (left_behind / "audit.md").write_text("secrets", encoding="utf-8")

    r = client.delete("/projects/del-ws")

    assert r.status_code == 200, r.text
    assert not (ws_root / "del-ws").exists(), "워크스페이스 디렉터리가 남았다"


def test_delete_removes_the_workspace_dir_when_the_runner_is_attached(
        monkeypatch, tmp_path):
    """attach된 정상 경로에서도 같은 결과여야 한다 — 대칭 확인.

    이쪽은 `runner.stop()`이 이미 지우지만, 그 rmtree는 드라이버 종료와 한 몸이라
    stop이 실패하면 함께 건너뛰어진다(그 실패는 의도적으로 삼킨다). 경로 기반
    삭제가 그 뒤를 받는다.
    """
    sessions, root = FakeS3Store(), FakeS3Store()
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    ws_root = tmp_path / "workspaces"
    monkeypatch.setattr(app_module, "_workspaces_dir", lambda: ws_root)

    class _BrokenRunner:
        async def stop(self):
            raise RuntimeError("driver disconnect failed")

    app_module.registry.register("del-ws2")
    app_module.registry.attach("del-ws2", Workspace(_BrokenRunner()))
    root.blobs["del-ws2/project.json"] = "{}"
    (ws_root / "del-ws2").mkdir(parents=True)

    r = client.delete("/projects/del-ws2")

    assert r.status_code == 200, r.text
    assert not (ws_root / "del-ws2").exists()


def test_delete_removes_the_projects_prototype_root(monkeypatch, _proto_wiring):
    """슬러그를 다 지운 뒤 **부모 디렉터리**도 사라져야 한다.

    `host.purge`가 지우는 것은 `{root}/{pid}/{slug}`뿐이라(host.py) 빈 껍데기가
    남았다. 부모를 슬러그 루프 **뒤에** 지우는 것이 중요하다 — 먼저 지우면 도는
    `npm start` 밑에서 트리가 사라져 프로세스가 고아가 되고 포트를 계속 물고 있다
    (`host.purge`가 `stop`을 먼저 부르는 이유와 같은 위험).
    """
    sessions, root = FakeS3Store(), FakeS3Store()
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    monkeypatch.setattr(app_module, "_workspaces_dir",
                        lambda: _proto_wiring["proto_root"] / "ws")
    _seed_project("del-pr", sessions, root)
    _seed_prototype(_proto_wiring, "del-pr", "demo", "tok-pr")

    r = client.delete("/projects/del-pr")

    assert r.status_code == 200, r.text
    assert not (_proto_wiring["proto_root"] / "del-pr").exists(), (
        "프로토타입 부모 디렉터리가 남았다")


def test_delete_does_not_purge_the_prototype_root_when_a_slug_failed(
        monkeypatch, _proto_wiring):
    """슬러그 하나가 실패하면 부모를 지우지 않는다.

    부모를 지우면 남은 슬러그의 빌드 트리가 함께 사라지고, 재시도가 회수해야 할
    실체가 없어진다 — 슬러그별 게이트(설문 purge 실패 시 `continue`)와 같은
    판단이다. 그리고 `purge_project`는 아무것도 멈추지 않으므로, 실패한 슬러그가
    아직 프로세스를 쥐고 있으면 그 트리를 지우는 것은 포트를 물고 있는 고아를
    만드는 일이다(`ProtoHost.purge`가 `stop`을 먼저 부르는 이유).
    """
    sessions, root = FakeS3Store(), FakeS3Store()
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: sessions)
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    _seed_project("del-gate", sessions, root)
    _seed_prototype(_proto_wiring, "del-gate", "demo", "tok-gate")
    _proto_wiring["host"].purge_exc = RuntimeError("rmtree denied")

    r = client.delete("/projects/del-gate")

    assert r.status_code == 500
    assert _proto_wiring["host"].purged_projects == [], (
        "슬러그가 실패했는데 부모를 지웠다")
    # 재시도가 의미를 갖도록 S3와 레지스트리가 남아 있어야 한다.
    assert app_module.registry.is_registered("del-gate")
    assert any(k.startswith("del-gate/") for k in root.blobs)
    app_module.registry.remove("del-gate")
