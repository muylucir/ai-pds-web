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


def test_lifespan_restores_the_generated_language(monkeypatch):
    # 복원 루프가 language를 레지스트리에 넘기지 않으면 재시작마다 모든
    # 프로젝트가 조용히 한국어로 돌아간다 — 매니페스트에는 en이 그대로 있는데
    # 화면과 생성물만 달라지므로 눈치채기 어렵다. 구 매니페스트(language 키
    # 없음)가 ko로 떨어지는 것도 같은 루프가 책임진다.
    fake = FakeS3Store()
    fake.blobs["restored-en/project.json"] = json.dumps(
        {"project_id": "restored-en", "name": None, "language": "en"})
    fake.blobs["restored-legacy/project.json"] = json.dumps(
        {"project_id": "restored-legacy", "name": None})
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: fake)
    try:
        with TestClient(app_module.app):
            assert app_module.registry.get_language("restored-en") == "en"
            assert app_module.registry.get_language("restored-legacy") == "ko"
    finally:
        # 레지스트리는 모듈 전역이다 — 남겨 두면 다른 테스트의 목록에 샌다.
        app_module.registry.remove("restored-en")
        app_module.registry.remove("restored-legacy")


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
