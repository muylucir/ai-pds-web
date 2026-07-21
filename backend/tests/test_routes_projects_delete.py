# backend/tests/test_routes_projects_delete.py
from fastapi.testclient import TestClient
from pathfinder import app as app_module
from pathfinder.workspace import Workspace
from tests.fakes.in_memory_s3 import FakeS3Store

client = TestClient(app_module.app)


class _FakeRunner:
    def __init__(self):
        self.stopped = 0

    async def stop(self):
        self.stopped += 1


def _seed_project(pid: str, sessions: FakeS3Store, root: FakeS3Store) -> _FakeRunner:
    runner = _FakeRunner()
    app_module.registry.register(pid)
    app_module.registry.attach(pid, Workspace(runner))
    sessions.blobs[f"session_{pid}/agents/agent_default/messages/message_0.json"] = "{}"
    root.blobs[f"{pid}/project.json"] = "{}"
    root.blobs[f"{pid}/aiplc-docs/audit.md"] = "x"
    return runner


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
