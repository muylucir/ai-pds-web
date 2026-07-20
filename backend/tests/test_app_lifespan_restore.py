# backend/tests/test_app_lifespan_restore.py
import json
from fastapi.testclient import TestClient
from pathfinder import app as app_module
from tests.fakes.in_memory_s3 import FakeS3Store


def test_lifespan_restores_registered_projects(monkeypatch):
    fake = FakeS3Store()
    fake.blobs["restored-1/project.json"] = json.dumps(
        {"project_id": "restored-1", "name": "복원된 프로젝트"})
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: fake)
    # with-구문이 lifespan을 실행한다 (모듈 레벨 TestClient는 실행 안 함)
    with TestClient(app_module.app) as client:
        r = client.get("/projects")
        assert r.status_code == 200
        by_id = {p["project_id"]: p["name"] for p in r.json()["projects"]}
        assert by_id["restored-1"] == "복원된 프로젝트"
    # 복원된 프로젝트는 목록에만 있고 sandbox는 없다 (lazy는 Task 7)
    assert app_module.registry.is_registered("restored-1")
    assert not app_module.registry.has_workspace("restored-1")


def test_lifespan_skips_restore_without_bucket(monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    called = {"n": 0}

    def _boom():
        called["n"] += 1
        raise AssertionError("must not be called")

    monkeypatch.setattr(app_module, "projects_root_s3_factory", _boom)
    with TestClient(app_module.app):
        pass
    assert called["n"] == 0


def test_lifespan_survives_restore_failure(monkeypatch):
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")

    class _ExplodingStore:
        async def list(self, prefix):
            raise RuntimeError("s3 down")

    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: _ExplodingStore())
    with TestClient(app_module.app) as client:  # 기동이 죽으면 여기서 예외
        assert client.get("/projects").status_code == 200
