# backend/tests/test_routes_design.py
#
# 라우트 계층의 책임만: 업로드 검증의 HTTP 번역, 응답 모양, 관리자 게이트.
# 파싱 자체는 test_design_profile.py가 본다.
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import pathfinder.app as app_module
from pathfinder.auth.deps import require_admin, require_user
from pathfinder.auth.models import Principal
from pathfinder.design_profile import DesignProfileStore
from tests.fakes.in_memory_s3 import FakeS3Store

GOOD_MD = "```tokens\nprimary: #5b2ea6\n```\n## 톤\n여백을 넉넉히.\n"


@pytest.fixture()
def profiles(monkeypatch):
    store = DesignProfileStore(FakeS3Store())
    monkeypatch.setattr(app_module, "design_profile_store", lambda: store)
    me = Principal(username="admin@pathfinder.local", sub="s-admin", role="admin")
    app_module.app.dependency_overrides[require_admin] = lambda: me
    app_module.app.dependency_overrides[require_user] = lambda: me
    yield store
    app_module.app.dependency_overrides.clear()


@pytest.fixture()
def client():
    return TestClient(app_module.app)


def test_absent_profile_is_null_not_404(profiles, client):
    res = client.get("/admin/design")
    assert res.status_code == 200
    assert res.json() == {"profile": None}


def test_upload_returns_parsed_tokens_and_prose(profiles, client):
    res = client.put("/admin/design", files={
        "file": ("acme.md", GOOD_MD.encode("utf-8"), "text/markdown")})
    assert res.status_code == 200
    body = res.json()["profile"]
    assert body["filename"] == "acme.md"
    assert body["tokens"] == {"primary": "#5b2ea6"}
    assert "여백을 넉넉히" in body["prose"]
    assert body["uploaded_by"] == "admin@pathfinder.local"
    # 원문은 이 응답에 넣지 않는다 — 화면은 /raw로 내려받는다.
    assert "markdown" not in body


def test_upload_reports_the_offending_line(profiles, client):
    bad = "```tokens\nprimary: #fff\nbrand: #123456\n```\n"
    res = client.put("/admin/design", files={
        "file": ("bad.md", bad.encode("utf-8"), "text/markdown")})
    assert res.status_code == 400
    assert "line 3" in res.json()["detail"]


def test_non_markdown_is_rejected(profiles, client):
    res = client.put("/admin/design", files={
        "file": ("brand.pdf", b"%PDF-1.4", "application/pdf")})
    assert res.status_code == 415


def test_non_utf8_is_rejected(profiles, client):
    res = client.put("/admin/design", files={
        "file": ("acme.md", b"\xff\xfe\x00bad", "text/markdown")})
    assert res.status_code == 415


def test_oversized_upload_is_rejected(profiles, client):
    from pathfinder.design_profile import MAX_DESIGN_BYTES
    res = client.put("/admin/design", files={
        "file": ("big.md", b"a" * (MAX_DESIGN_BYTES + 1), "text/markdown")})
    assert res.status_code == 413


def test_delete_removes_it(profiles, client):
    client.put("/admin/design", files={
        "file": ("acme.md", GOOD_MD.encode("utf-8"), "text/markdown")})
    assert client.delete("/admin/design").status_code == 204
    assert client.get("/admin/design").json() == {"profile": None}


def test_raw_returns_the_original_markdown(profiles, client):
    client.put("/admin/design", files={
        "file": ("acme.md", GOOD_MD.encode("utf-8"), "text/markdown")})
    res = client.get("/admin/design/raw")
    assert res.status_code == 200
    assert res.text == GOOD_MD
    assert "attachment" in res.headers["content-disposition"]


def test_raw_is_404_when_absent(profiles, client):
    assert client.get("/admin/design/raw").status_code == 404


def test_template_is_always_available(profiles, client):
    res = client.get("/admin/design/template")
    assert res.status_code == 200
    assert "```tokens" in res.text


def test_pm_is_forbidden(profiles, client):
    me = Principal(username="pm@x", sub="s-pm", role="pm")
    app_module.app.dependency_overrides.pop(require_admin, None)
    app_module.app.dependency_overrides[require_user] = lambda: me
    assert client.get("/admin/design").status_code == 403
