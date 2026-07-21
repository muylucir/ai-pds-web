import json
from fastapi.testclient import TestClient
from pathfinder import app as app_module
from pathfinder.workspace import Workspace
from tests.fakes.in_memory_s3 import FakeS3Store

client = TestClient(app_module.app)


def test_create_writes_manifest_when_durable(monkeypatch):
    fake = FakeS3Store()
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: fake)
    r = client.post("/projects", json={"project_id": "persist-1", "name": "이름"})
    assert r.status_code == 200
    d = json.loads(fake.blobs["persist-1/project.json"])
    assert d["project_id"] == "persist-1" and d["name"] == "이름"


def test_create_without_bucket_writes_no_manifest(monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    r = client.post("/projects", json={"project_id": "persist-2"})
    assert r.status_code == 200  # 로컬 모드: 매니페스트 생략, 기존 동작


def test_create_fails_500_when_manifest_write_fails(monkeypatch):
    class _ExplodingStore:
        async def put(self, key, content):
            raise RuntimeError("s3 down")

    stopped = {"n": 0}

    class _FakeRunner:
        async def stop(self):
            stopped["n"] += 1

    async def _fake_make_workspace(pid):
        return Workspace(_FakeRunner())

    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: _ExplodingStore())
    monkeypatch.setattr(app_module, "make_workspace", _fake_make_workspace)
    r = client.post("/projects", json={"project_id": "persist-3"})
    assert r.status_code == 500
    assert stopped["n"] == 1                                # 베스트에포트 정리
    assert not app_module.registry.is_registered("persist-3")  # 조용한 휘발 프로젝트 금지
