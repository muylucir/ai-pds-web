# backend/tests/test_routes_projects_model.py
#
# 생성 시점의 model_id 검증과 조회. 검증 기준이 '표시 목록'인 것이 핵심이다 —
# 표시가 꺼진 모델은 관리자가 의도적으로 내린 것이므로 새 프로젝트가 그것을
# 고르면 안 된다.
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import aipds.app as app_module
from aipds.model_catalog import SEED_MODELS, ModelCatalog
from tests.fakes.in_memory_s3 import FakeS3Store

client = TestClient(app_module.app)
CHOSEN = SEED_MODELS[0].model_id


@pytest.fixture()
def catalog(monkeypatch):
    cat = ModelCatalog(FakeS3Store())
    monkeypatch.setattr(app_module, "model_catalog", lambda: cat)
    return cat


@pytest.fixture(autouse=True)
def cleanup():
    yield
    for pid in ("pm-1", "pm-2", "pm-3", "pm-4", "pm-5"):
        app_module.registry.remove(pid)


def test_create_accepts_a_displayed_model_and_records_it(catalog, monkeypatch):
    fake = FakeS3Store()
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: fake)
    r = client.post("/projects", json={"project_id": "pm-1", "model_id": CHOSEN})
    assert r.status_code == 200
    assert r.json()["model_id"] == CHOSEN
    assert json.loads(fake.blobs["pm-1/project.json"])["model_id"] == CHOSEN
    assert app_module.registry.get_model_id("pm-1") == CHOSEN


def test_create_without_a_model_id_still_works(catalog, monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    r = client.post("/projects", json={"project_id": "pm-2"})
    assert r.status_code == 200
    assert r.json()["model_id"] is None
    assert app_module.registry.get_model_id("pm-2") is None


def test_create_rejects_an_unregistered_model_id(catalog, monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    r = client.post("/projects", json={"project_id": "pm-3",
                                       "model_id": "global.anthropic.claude-nope"})
    assert r.status_code == 400
    # 첫 대화 턴의 AccessDenied가 아니라 생성 시점에 막혀야 한다.
    assert not app_module.registry.is_registered("pm-3")


@pytest.mark.asyncio
async def test_create_rejects_a_hidden_model_id(catalog, monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    await catalog.update(CHOSEN, display=False)
    r = client.post("/projects", json={"project_id": "pm-4", "model_id": CHOSEN})
    assert r.status_code == 400


def test_get_project_returns_metadata_without_booting_a_workspace(catalog, monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    booted = {"n": 0}

    async def _boom(pid):
        booted["n"] += 1
        raise AssertionError("배지 하나가 워크스페이스 lazy 부팅을 유발해서는 안 된다")

    app_module.registry.register("pm-5", "이름",
                                 created_at="2026-08-01T00:00:00+00:00",
                                 model_id=CHOSEN)
    monkeypatch.setattr(app_module, "make_workspace", _boom)
    body = client.get("/projects/pm-5").json()
    assert body == {"project_id": "pm-5", "name": "이름",
                    "created_at": "2026-08-01T00:00:00+00:00",
                    "model_id": CHOSEN,
                    # language 없이 register된 프로젝트 — get_language가 "ko"로 확정한다.
                    "language": "ko"}
    assert booted["n"] == 0


def test_get_project_is_404_for_an_unknown_project(catalog):
    assert client.get("/projects/never-existed").status_code == 404


def test_list_includes_model_id(catalog, monkeypatch):
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    client.post("/projects", json={"project_id": "pm-1", "model_id": CHOSEN})
    rows = client.get("/projects?page=1&size=50").json()["projects"]
    row = next(p for p in rows if p["project_id"] == "pm-1")
    assert row["model_id"] == CHOSEN
