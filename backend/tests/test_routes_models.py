# backend/tests/test_routes_models.py
#
# Only the route layer's responsibilities: response trimming (/models returns display only, names and ids only), HTTP
# translation of policy violations, and the admin gate. The catalog itself is covered by test_model_catalog.py.
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import aipds.app as app_module
from aipds.auth.deps import require_admin, require_user
from aipds.auth.models import Principal
from aipds.model_catalog import SEED_MODELS, ModelCatalog
from tests.fakes.in_memory_s3 import FakeS3Store


@pytest.fixture()
def catalog(monkeypatch):
    """A writable catalog + a requester 'logged in as admin'."""
    cat = ModelCatalog(FakeS3Store())
    monkeypatch.setattr(app_module, "model_catalog", lambda: cat)
    me = Principal(username="admin@aipds.local", sub="s-admin", role="admin")
    app_module.app.dependency_overrides[require_admin] = lambda: me
    app_module.app.dependency_overrides[require_user] = lambda: me
    yield cat
    app_module.app.dependency_overrides.clear()


@pytest.fixture()
def client():
    return TestClient(app_module.app)


# ---- GET /models (regular) ----

def test_models_returns_name_id_and_effort_only(catalog, client):
    body = client.get("/models").json()
    # effort is sent — even for the same model, speed varies greatly with effort, so whoever picks needs to know it.
    assert body["models"] == [
        {"name": e.name, "model_id": e.model_id, "effort": e.effort} for e in SEED_MODELS]
    # display is not sent to the screen — it means nothing to regular users, and this removes the path
    # where the frontend forgets to filter.
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
                                 "display": True, "effort": SEED_MODELS[0].effort}


# ---- POST /admin/models ----

def test_admin_add_returns_201_and_the_entry(catalog, client):
    r = client.post("/admin/models",
                    json={"name": "Opus 4.8",
                          "model_id": "global.anthropic.claude-opus-4-8",
                          "display": False})
    assert r.status_code == 201
    assert r.json() == {"name": "Opus 4.8",
                        "model_id": "global.anthropic.claude-opus-4-8",
                        "display": False, "effort": None}


def test_admin_add_records_the_effort(catalog, client):
    r = client.post("/admin/models", json={"name": "Opus 4.8",
                                           "model_id": "global.anthropic.claude-opus-4-8",
                                           "effort": "xhigh"})
    assert r.status_code == 201 and r.json()["effort"] == "xhigh"


def test_admin_add_rejects_an_effort_the_cli_does_not_know(catalog, client):
    r = client.post("/admin/models", json={"name": "Opus 4.8",
                                           "model_id": "global.anthropic.claude-opus-4-8",
                                           "effort": "turbo"})
    assert r.status_code == 422


def test_admin_patch_sets_clears_and_keeps_effort(catalog, client):
    mid = SEED_MODELS[0].model_id
    assert client.patch(f"/admin/models/{mid}", json={"effort": "high"}).json()["effort"] == "high"
    # If effort is not sent, it stays as is.
    assert client.patch(f"/admin/models/{mid}", json={"display": True}).json()["effort"] == "high"
    # null clears it to the CLI default.
    assert client.patch(f"/admin/models/{mid}", json={"effort": None}).json()["effort"] is None
    assert client.patch(f"/admin/models/{mid}", json={"effort": "turbo"}).status_code == 422


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
    # The sentence must say what the admin needs to do.
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
    # "   " passes Pydantic's min_length=1 (whitespace is a character too) — without an explicit
    # strip validation symmetric with name, an empty-string model_id would be registered, permanently
    # occupying a display slot, and could not be deleted via the API either.
    r = client.post("/admin/models", json={
        "name": "x", "model_id": "   ", "display": True})
    assert r.status_code == 422
    # Whitespace-only must get the "empty" message — not the charset violation message.
    # This is a pin test fixing the order of the two validations.
    # detail is a stable code — the wording is owned by the frontend dictionary
    # (see the backend/aipds/error_codes.py header).
    assert r.json()["detail"] == "model_id_required"


def test_admin_add_rejects_a_model_id_with_a_slash(catalog, client):
    # PATCH/DELETE /admin/models/{model_id} take {model_id} as a single segment, not
    # :path — an id containing '/' can be registered but can never be found again by
    # that path afterward (a single typo would permanently occupy a display slot
    # with no way to delete it via the API). It is blocked at registration time.
    r = client.post("/admin/models", json={
        "name": "x", "model_id": "oops/typed/slash", "display": True})
    assert r.status_code == 422


def test_admin_add_rejects_a_model_id_with_an_internal_space(catalog, client):
    r = client.post("/admin/models", json={
        "name": "x", "model_id": "has space", "display": True})
    assert r.status_code == 422


@pytest.mark.parametrize("dots", [".", ".."])
def test_admin_add_rejects_a_dot_segment_model_id(catalog, client, dots):
    # Rejected even when made only of allowed characters ('.'). RFC 3986's dot-segment normalization
    # happens on the **client**, so these two values can be registered but a standard client cannot
    # construct and send that path — observed with the WHATWG URL parser the frontend uses:
    #   '/admin/models/.'  -> '/admin/models/'
    #   '/admin/models/..' -> '/admin/'
    # Just like an id containing '/', it becomes an entry "that can be registered but not deleted via the API".
    r = client.post("/admin/models", json={
        "name": "x", "model_id": dots, "display": True})
    assert r.status_code == 422


def test_admin_add_accepts_dots_that_are_not_dot_segments(catalog, client):
    # The normalization targets are exactly the '.' and '..' segments — '...' and 'abc.'
    # stay in the path as is, so they are not over-rejected (if the guard grew
    # wider than needed, it would wrongly block real model ids).
    for mid in ("...", "abc.", ".abc"):
        r = client.post("/admin/models", json={
            "name": mid, "model_id": mid, "display": False})
        assert r.status_code == 201, mid


def test_admin_add_accepts_every_legal_model_id_character(catalog, client):
    # In addition to the charset the spec (§5) defines (alphanumerics, '.', '-', ':'), '_' is also allowed — it is
    # a legitimate character that can appear in AWS model ids and does not break route segments.
    # Colons and underscores are each checked to make sure the guard does not over-reject real id shapes
    # (ids that do not overlap SEED_MODELS are used — an overlap would be masked by a 409).
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


# ---- PUT /admin/models/order ----

def test_admin_reorder_returns_the_new_order(catalog, client):
    ids = [e.model_id for e in SEED_MODELS][::-1]
    r = client.put("/admin/models/order", json={"model_ids": ids})
    assert r.status_code == 200
    assert [m["model_id"] for m in r.json()["models"]] == ids
    assert [m["model_id"] for m in client.get("/models").json()["models"]] == ids


def test_admin_reorder_with_a_stale_list_is_409_with_a_code(catalog, client):
    ids = [e.model_id for e in SEED_MODELS][1:]
    r = client.put("/admin/models/order", json={"model_ids": ids})
    assert r.status_code == 409
    # The wording is owned by the frontend dictionary (error_codes.py header).
    assert r.json()["detail"] == "model_order_stale"


# ---- DELETE /admin/models/{model_id} ----

def test_admin_delete_removes_the_entry(catalog, client):
    r = client.delete(f"/admin/models/{SEED_MODELS[0].model_id}")
    assert r.status_code == 204
    ids = {m["model_id"] for m in client.get("/admin/models").json()["models"]}
    assert SEED_MODELS[0].model_id not in ids


def test_admin_delete_unknown_model_is_404(catalog, client):
    assert client.delete("/admin/models/global.anthropic.claude-nope").status_code == 404


# ---- Bucket not configured ----

def test_admin_write_without_a_bucket_is_503(monkeypatch, client):
    monkeypatch.setattr(app_module, "model_catalog", lambda: ModelCatalog(None))
    me = Principal(username="admin@aipds.local", sub="s-admin", role="admin")
    app_module.app.dependency_overrides[require_admin] = lambda: me
    app_module.app.dependency_overrides[require_user] = lambda: me
    try:
        r = client.post("/admin/models", json={
            "name": "x", "model_id": "global.anthropic.claude-opus-4-8",
            "display": True})
        assert r.status_code == 503
        # Reads still work — local development runs on the seed.
        assert len(client.get("/models").json()["models"]) == len(SEED_MODELS)
    finally:
        app_module.app.dependency_overrides.clear()
