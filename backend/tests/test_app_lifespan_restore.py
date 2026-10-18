# backend/tests/test_app_lifespan_restore.py
import json
from fastapi.testclient import TestClient
from aipds import app as app_module
from tests.fakes.in_memory_s3 import FakeS3Store


def test_lifespan_restores_registered_projects(monkeypatch):
    fake = FakeS3Store()
    fake.blobs["restored-1/project.json"] = json.dumps(
        {"project_id": "restored-1", "name": "복원된 프로젝트"})
    monkeypatch.setenv("AIPDS_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: fake)
    # The with statement runs the lifespan (a module-level TestClient does not)
    with TestClient(app_module.app) as client:
        r = client.get("/projects")
        assert r.status_code == 200
        by_id = {p["project_id"]: p["name"] for p in r.json()["projects"]}
        assert by_id["restored-1"] == "복원된 프로젝트"
    # Restored projects are only in the list and have no sandbox (lazy is Task 7)
    assert app_module.registry.is_registered("restored-1")
    assert not app_module.registry.has_workspace("restored-1")


def test_lifespan_restores_the_generated_language(monkeypatch):
    # If the restore loop did not pass language to the registry, every project would silently
    # revert to Korean on each restart — the manifest still says en, and only the screen and
    # output change, so it is hard to notice. Old manifests (no language key) falling to ko is
    # also the responsibility of the same loop.
    fake = FakeS3Store()
    fake.blobs["restored-en/project.json"] = json.dumps(
        {"project_id": "restored-en", "name": None, "language": "en"})
    fake.blobs["restored-legacy/project.json"] = json.dumps(
        {"project_id": "restored-legacy", "name": None})
    monkeypatch.setenv("AIPDS_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: fake)
    try:
        with TestClient(app_module.app):
            assert app_module.registry.get_language("restored-en") == "en"
            assert app_module.registry.get_language("restored-legacy") == "ko"
    finally:
        # The registry is module-global — if left in place, it leaks into other tests' lists.
        app_module.registry.remove("restored-en")
        app_module.registry.remove("restored-legacy")


def test_lifespan_skips_restore_without_bucket(monkeypatch):
    monkeypatch.delenv("AIPDS_S3_BUCKET", raising=False)
    called = {"n": 0}

    def _boom():
        called["n"] += 1
        raise AssertionError("must not be called")

    monkeypatch.setattr(app_module, "projects_root_s3_factory", _boom)
    with TestClient(app_module.app):
        pass
    assert called["n"] == 0


def test_lifespan_survives_restore_failure(monkeypatch):
    monkeypatch.setenv("AIPDS_S3_BUCKET", "some-bucket")

    class _ExplodingStore:
        async def list(self, prefix):
            raise RuntimeError("s3 down")

    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: _ExplodingStore())
    with TestClient(app_module.app) as client:  # raises here if startup dies
        assert client.get("/projects").status_code == 200
