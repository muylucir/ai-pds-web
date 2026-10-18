# backend/tests/test_routes_projects_language.py
#
# Validation and lookup of language at creation time. It uses the same plumbing as
# model_id but a different validation basis — two fixed values, not a catalog.
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import aipds.app as app_module
from tests.fakes.in_memory_s3 import FakeS3Store

client = TestClient(app_module.app)


@pytest.fixture(autouse=True)
def cleanup():
    yield
    for pid in ("pl-1", "pl-2", "pl-3", "pl-4", "pl-5"):
        app_module.registry.remove(pid)


def test_create_accepts_en_and_records_it(monkeypatch):
    fake = FakeS3Store()
    monkeypatch.setenv("AIPDS_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: fake)
    r = client.post("/projects", json={"project_id": "pl-1", "language": "en"})
    assert r.status_code == 200
    assert r.json()["language"] == "en"
    assert json.loads(fake.blobs["pl-1/project.json"])["language"] == "en"
    assert app_module.registry.get_language("pl-1") == "en"


def test_create_without_a_language_defaults_to_ko(monkeypatch):
    monkeypatch.delenv("AIPDS_S3_BUCKET", raising=False)
    r = client.post("/projects", json={"project_id": "pl-2"})
    assert r.status_code == 200
    # The response states the language that will actually run — returning null for
    # unspecified would force the frontend to know the fallback rule as well.
    assert r.json()["language"] == "ko"
    assert app_module.registry.get_language("pl-2") == "ko"


def test_create_rejects_an_unknown_language(monkeypatch):
    monkeypatch.delenv("AIPDS_S3_BUCKET", raising=False)
    r = client.post("/projects", json={"project_id": "pl-3", "language": "ja"})
    assert r.status_code == 400
    # detail is a stable code — the frontend dictionary looks up its message by this value.
    assert r.json()["detail"] == "language_unsupported"
    assert not app_module.registry.is_registered("pl-3")


def test_get_project_includes_the_language(monkeypatch):
    monkeypatch.delenv("AIPDS_S3_BUCKET", raising=False)
    app_module.registry.register("pl-4", "이름",
                                 created_at="2026-08-03T00:00:00+00:00",
                                 language="en")
    body = client.get("/projects/pl-4").json()
    assert body["language"] == "en"


def test_list_includes_the_language(monkeypatch):
    monkeypatch.delenv("AIPDS_S3_BUCKET", raising=False)
    client.post("/projects", json={"project_id": "pl-5", "language": "en"})
    rows = client.get("/projects?page=1&size=50").json()["projects"]
    row = next(p for p in rows if p["project_id"] == "pl-5")
    assert row["language"] == "en"


def test_project_language_helper_reads_the_registry():
    app_module.registry.register("pl-1", None, language="en")
    assert app_module.project_language("pl-1") == "en"
    # Unregistered is also ko — whatever the registry settles on is passed through as is.
    assert app_module.project_language("never-existed") == "ko"


# ---- Creation order: is the driver actually built with that language? ----


def test_the_created_projects_driver_gets_the_chosen_language(monkeypatch):
    """**This is the defect that remained on 2026-08-04.**

    The tests above look only at the manifest and the registry — both get "en" correctly.
    But the **driver** that actually runs the conversation never received that value.

    The order in create_project was the cause:

        workspace = await app_module.make_workspace(...)   # driver_factory ->
                                                          # project_language(pid)
        ...
        app_module.registry.register(..., language=...)    # the value arrives here

    Because make_workspace runs **before** register, the registry.get_language(pid) that
    driver_factory calls returns the fallback "ko" while the registry still knows
    nothing. And that driver is cached for the lifetime of the process (the Workspace
    attached to the registry holds it), so **every turn** of a newly created English
    project runs with Korean instructions.

    Why the symptom was especially confusing: the language badge in the header reads the
    manifest, so it correctly shows "English". This is the state where the screen is in
    English but only the conversation is in Korean.

    The same ordering problem exists for model_id (test_the_created_projects_driver_gets_
    the_chosen_model). Unlike language, the model has an env fallback, so it silently ran a different model.
    """
    monkeypatch.delenv("AIPDS_S3_BUCKET", raising=False)
    seen: dict = {}

    def spy_factory(pid, local_root):
        seen["language"] = app_module.project_language(pid)
        return object()

    monkeypatch.setattr(app_module, "driver_factory", spy_factory)
    r = client.post("/projects", json={"project_id": "pl-1", "language": "en"})
    assert r.status_code == 200
    assert seen["language"] == "en", (
        "드라이버가 'ko'로 만들어졌다 — 매니페스트와 배지는 en인데 대화는 한국어로 "
        "돈다. make_workspace가 registry.register보다 먼저 도는 순서 문제다.")


def test_the_created_projects_driver_gets_the_chosen_model(monkeypatch):
    """The model_id version of the same ordering defect. Harder to find than language —
    project_model has an env fallback (ANTHROPIC_MODEL), so it silently runs on the
    deployment's default model instead of the chosen one (no error, no log)."""
    monkeypatch.delenv("AIPDS_S3_BUCKET", raising=False)
    monkeypatch.setenv("ANTHROPIC_MODEL", "env-default-model")
    monkeypatch.setattr(app_module, "_validate_model_id",
                        lambda *a, **k: None, raising=False)

    import aipds.routes.projects as routes_projects
    async def ok(_model_id):
        return None
    monkeypatch.setattr(routes_projects, "_validate_model_id", ok)

    seen: dict = {}

    def spy_factory(pid, local_root):
        seen["model"] = app_module.project_model(pid)
        return object()

    monkeypatch.setattr(app_module, "driver_factory", spy_factory)
    r = client.post("/projects", json={"project_id": "pl-2",
                                       "model_id": "chosen-model"})
    assert r.status_code == 200
    assert seen["model"] == "chosen-model", (
        f"드라이버가 {seen['model']!r}로 만들어졌다 — 고른 모델이 무시되고 env "
        "기본값으로 돈다.")
