# backend/tests/test_routes_projects_language.py
#
# 생성 시점의 language 검증과 조회. model_id와 같은 배관을 쓰지만 검증 기준이
# 다르다 — 카탈로그가 아니라 고정된 두 값이다.
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import pathfinder.app as app_module
from tests.fakes.in_memory_s3 import FakeS3Store

client = TestClient(app_module.app)


@pytest.fixture(autouse=True)
def cleanup():
    yield
    for pid in ("pl-1", "pl-2", "pl-3", "pl-4", "pl-5"):
        app_module.registry.remove(pid)


def test_create_accepts_en_and_records_it(monkeypatch):
    fake = FakeS3Store()
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: fake)
    r = client.post("/projects", json={"project_id": "pl-1", "language": "en"})
    assert r.status_code == 200
    assert r.json()["language"] == "en"
    assert json.loads(fake.blobs["pl-1/project.json"])["language"] == "en"
    assert app_module.registry.get_language("pl-1") == "en"


def test_create_without_a_language_defaults_to_ko(monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    r = client.post("/projects", json={"project_id": "pl-2"})
    assert r.status_code == 200
    # 응답은 실제로 돌게 될 언어를 말한다 — 미지정을 null로 돌려주면 프론트가
    # 폴백 규칙을 또 알아야 한다.
    assert r.json()["language"] == "ko"
    assert app_module.registry.get_language("pl-2") == "ko"


def test_create_rejects_an_unknown_language(monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    r = client.post("/projects", json={"project_id": "pl-3", "language": "ja"})
    assert r.status_code == 400
    assert not app_module.registry.is_registered("pl-3")


def test_get_project_includes_the_language(monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    app_module.registry.register("pl-4", "이름",
                                 created_at="2026-08-03T00:00:00+00:00",
                                 language="en")
    body = client.get("/projects/pl-4").json()
    assert body["language"] == "en"


def test_list_includes_the_language(monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    client.post("/projects", json={"project_id": "pl-5", "language": "en"})
    rows = client.get("/projects?page=1&size=50").json()["projects"]
    row = next(p for p in rows if p["project_id"] == "pl-5")
    assert row["language"] == "en"


def test_project_language_helper_reads_the_registry():
    app_module.registry.register("pl-1", None, language="en")
    assert app_module.project_language("pl-1") == "en"
    # 미등록도 ko — 레지스트리가 확정하는 것을 그대로 통과시킨다.
    assert app_module.project_language("never-existed") == "ko"
