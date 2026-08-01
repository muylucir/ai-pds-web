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


def test_admin_add_rejects_a_whitespace_only_model_id(catalog, client):
    # "   "는 Pydantic의 min_length=1을 통과한다(공백도 문자다) — name과
    # 대칭인 명시적 strip 검증이 없으면 빈 문자열 model_id가 등록되어 표시
    # 슬롯을 영구히 점유하고 API로 지울 수도 없다.
    r = client.post("/admin/models", json={
        "name": "x", "model_id": "   ", "display": True})
    assert r.status_code == 422
    # 공백 전용은 "비었다"는 메시지여야 한다 — 문자셋 위반 메시지가 아니라.
    # 두 검증의 순서를 못박는 핀 테스트다.
    assert r.json()["detail"] == "모델 ID를 입력하세요."


def test_admin_add_rejects_a_model_id_with_a_slash(catalog, client):
    # PATCH/DELETE /admin/models/{model_id}가 {model_id}를 :path가 아닌
    # 단일 세그먼트로 받는다 — '/'가 들어간 id는 등록은 되지만 이후 그
    # 경로로는 절대 다시 찾을 수 없다(오탈자 하나가 표시 슬롯을 영구히
    # 점유하고 API로 지울 수 없게 만든다). 등록 시점에 막는다.
    r = client.post("/admin/models", json={
        "name": "x", "model_id": "oops/typed/slash", "display": True})
    assert r.status_code == 422


def test_admin_add_rejects_a_model_id_with_an_internal_space(catalog, client):
    r = client.post("/admin/models", json={
        "name": "x", "model_id": "has space", "display": True})
    assert r.status_code == 422


@pytest.mark.parametrize("dots", [".", ".."])
def test_admin_add_rejects_a_dot_segment_model_id(catalog, client, dots):
    # 허용 문자('.')로만 되어 있어도 거부한다. RFC 3986의 dot-segment 정규화는
    # **클라이언트**에서 일어나므로 이 두 값은 등록은 되지만 표준 클라이언트가
    # 그 경로를 만들어 보낼 수 없다 — 프론트가 쓰는 WHATWG URL 파서 실측:
    #   '/admin/models/.'  -> '/admin/models/'
    #   '/admin/models/..' -> '/admin/'
    # '/'가 들어간 id와 똑같이 "등록은 되는데 API로 지울 수 없는" 항목이 된다.
    r = client.post("/admin/models", json={
        "name": "x", "model_id": dots, "display": True})
    assert r.status_code == 422


def test_admin_add_accepts_dots_that_are_not_dot_segments(catalog, client):
    # 정규화 대상은 정확히 '.'과 '..' 세그먼트뿐이다 — '...'이나 'abc.'는
    # 경로에 그대로 남으므로 과잉 거부하지 않는다(가드가 필요 이상으로
    # 넓어지면 실제 모델 id를 잘못 막는다).
    for mid in ("...", "abc.", ".abc"):
        r = client.post("/admin/models", json={
            "name": mid, "model_id": mid, "display": False})
        assert r.status_code == 201, mid


def test_admin_add_accepts_every_legal_model_id_character(catalog, client):
    # 스펙(§5)이 정한 문자셋(영숫자·.·-·:)에 더해 '_'도 허용한다 — AWS 모델
    # id에 나타날 수 있는 합법적 문자이고, 라우트 세그먼트를 깨지 않는다.
    # 가드가 실제 id 모양을 과잉 거부하지 않는지 콜론과 언더스코어를 각각
    # 확인한다(SEED_MODELS와 겹치지 않는 id를 쓴다 — 겹치면 409로 가려진다).
    r = client.post("/admin/models", json={
        "name": "콜론", "model_id": "anthropic.claude-3-sonnet-20240229-v1:0",
        "display": False})
    assert r.status_code == 201

    r = client.post("/admin/models", json={
        "name": "언더스코어",
        "model_id": "global.anthropic.claude-opus-5_v1",
        "display": False})
    assert r.status_code == 201


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
