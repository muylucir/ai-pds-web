# backend/tests/test_routes_models.py
#
# 라우트 계층의 책임만: 응답 축약(/models는 display만·이름과 id만), 정책 위반의
# HTTP 번역, 관리자 게이트. 카탈로그 자체는 test_model_catalog.py가 본다.
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import pathfinder.app as app_module
from pathfinder.auth.deps import require_admin, require_user
from pathfinder.auth.models import Principal
from pathfinder.model_catalog import SEED_MODELS, ModelCatalog
from tests.fakes.in_memory_s3 import FakeS3Store


@pytest.fixture()
def catalog(monkeypatch):
    """쓰기 가능한 카탈로그 + 'admin으로 로그인한' 요청자."""
    cat = ModelCatalog(FakeS3Store())
    monkeypatch.setattr(app_module, "model_catalog", lambda: cat)
    me = Principal(username="admin@pathfinder.local", sub="s-admin", role="admin")
    app_module.app.dependency_overrides[require_admin] = lambda: me
    app_module.app.dependency_overrides[require_user] = lambda: me
    yield cat
    app_module.app.dependency_overrides.clear()


@pytest.fixture()
def client():
    return TestClient(app_module.app)


# ---- GET /models (일반) ----

def test_models_returns_name_and_id_only(catalog, client):
    body = client.get("/models").json()
    assert body["models"] == [
        {"name": e.name, "model_id": e.model_id} for e in SEED_MODELS]
    # display는 화면에 보내지 않는다 — 일반 사용자에게 의미가 없고, 프론트가
    # 필터링을 잊는 경로를 없앤다.
    assert all("display" not in m for m in body["models"])


@pytest.mark.asyncio
async def test_models_omits_hidden_entries(catalog, client):
    await catalog.update(SEED_MODELS[0].model_id, display=False)
    ids = {m["model_id"] for m in client.get("/models").json()["models"]}
    assert SEED_MODELS[0].model_id not in ids
    assert SEED_MODELS[1].model_id in ids


# ---- GET /admin/models ----

def test_admin_list_includes_display_flag(catalog, client):
    body = client.get("/admin/models").json()
    assert body["models"][0] == {"name": SEED_MODELS[0].name,
                                 "model_id": SEED_MODELS[0].model_id,
                                 "display": True}


# ---- POST /admin/models ----

def test_admin_add_returns_201_and_the_entry(catalog, client):
    r = client.post("/admin/models",
                    json={"name": "Opus 4.8",
                          "model_id": "global.anthropic.claude-opus-4-8",
                          "display": False})
    assert r.status_code == 201
    assert r.json() == {"name": "Opus 4.8",
                        "model_id": "global.anthropic.claude-opus-4-8",
                        "display": False}


def test_admin_add_duplicate_is_409(catalog, client):
    r = client.post("/admin/models",
                    json={"name": "중복", "model_id": SEED_MODELS[0].model_id,
                          "display": False})
    assert r.status_code == 409


def test_admin_add_sixth_displayed_is_400(catalog, client):
    assert client.post("/admin/models", json={
        "name": "다섯", "model_id": "global.anthropic.claude-opus-4-8",
        "display": True}).status_code == 201
    r = client.post("/admin/models", json={
        "name": "여섯", "model_id": "global.anthropic.claude-opus-4-7",
        "display": True})
    assert r.status_code == 400
    # 관리자가 무엇을 해야 하는지 문장에 있어야 한다.
    assert "5" in r.json()["detail"]


def test_admin_add_rejects_a_blank_name(catalog, client):
    r = client.post("/admin/models", json={
        "name": "  ", "model_id": "global.anthropic.claude-opus-4-8",
        "display": True})
    assert r.status_code == 422


def test_admin_add_rejects_a_blank_model_id(catalog, client):
    r = client.post("/admin/models", json={"name": "x", "model_id": "",
                                           "display": True})
    assert r.status_code == 422


# ---- PATCH /admin/models/{model_id} ----

def test_admin_patch_changes_display(catalog, client):
    r = client.patch(f"/admin/models/{SEED_MODELS[0].model_id}",
                     json={"display": False})
    assert r.status_code == 200 and r.json()["display"] is False


def test_admin_patch_changes_name(catalog, client):
    r = client.patch(f"/admin/models/{SEED_MODELS[0].model_id}",
                     json={"name": "오퍼스 5"})
    assert r.status_code == 200 and r.json()["name"] == "오퍼스 5"


def test_admin_patch_unknown_model_is_404(catalog, client):
    r = client.patch("/admin/models/global.anthropic.claude-nope",
                     json={"display": False})
    assert r.status_code == 404


# ---- DELETE /admin/models/{model_id} ----

def test_admin_delete_removes_the_entry(catalog, client):
    r = client.delete(f"/admin/models/{SEED_MODELS[0].model_id}")
    assert r.status_code == 204
    ids = {m["model_id"] for m in client.get("/admin/models").json()["models"]}
    assert SEED_MODELS[0].model_id not in ids


def test_admin_delete_unknown_model_is_404(catalog, client):
    assert client.delete("/admin/models/global.anthropic.claude-nope").status_code == 404


# ---- 버킷 미설정 ----

def test_admin_write_without_a_bucket_is_503(monkeypatch, client):
    monkeypatch.setattr(app_module, "model_catalog", lambda: ModelCatalog(None))
    me = Principal(username="admin@pathfinder.local", sub="s-admin", role="admin")
    app_module.app.dependency_overrides[require_admin] = lambda: me
    app_module.app.dependency_overrides[require_user] = lambda: me
    try:
        r = client.post("/admin/models", json={
            "name": "x", "model_id": "global.anthropic.claude-opus-4-8",
            "display": True})
        assert r.status_code == 503
        # 읽기는 여전히 된다 — 로컬 개발이 시드로 돈다.
        assert len(client.get("/models").json()["models"]) == len(SEED_MODELS)
    finally:
        app_module.app.dependency_overrides.clear()
