# backend/tests/test_routes_design.py
#
# Only the route layer's responsibilities: HTTP translation of upload validation, response
# shape, the admin gate. Parsing itself is covered by test_design_profile.py.
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import aipds.app as app_module
from aipds.auth.deps import require_admin, require_user
from aipds.auth.models import Principal
from aipds.design_profile import DesignProfileStore
from tests.fakes.in_memory_s3 import FakeS3Store

GOOD_MD = "```tokens\nprimary: #5b2ea6\n```\n## 톤\n여백을 넉넉히.\n"


@pytest.fixture()
def profiles(monkeypatch):
    store = DesignProfileStore(FakeS3Store())
    monkeypatch.setattr(app_module, "design_profile_store", lambda: store)
    me = Principal(username="admin@aipds.local", sub="s-admin", role="admin")
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
    assert body["uploaded_by"] == "admin@aipds.local"
    # The raw document is not included in this response — the screen downloads it via /raw.
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
    from aipds.design_profile import MAX_DESIGN_BYTES
    res = client.put("/admin/design", files={
        "file": ("big.md", b"a" * (MAX_DESIGN_BYTES + 1), "text/markdown")})
    assert res.status_code == 413


def test_spoofed_content_length_rejected_before_body_read(profiles, client):
    # Reviewer-flagged gap (the counterpart in test_routes_uploads.py): in the
    # test_oversized_upload_is_rejected above, the body itself exceeds the limit, so the
    # re-check (len(data)) alone yields 413 even without the pre-check — the pre-check
    # branch could merely "exist" without ever having run. Here the body is kept small
    # and only the Content-Length header is forged, to confirm that the pre-check alone
    # yields 413 before the body is even read (TestClient/httpx sends an explicit
    # Content-Length override as is).
    from aipds.design_profile import MAX_DESIGN_BYTES
    res = client.put("/admin/design", files={
        "file": ("a.md", b"x", "text/markdown")},
        headers={"Content-Length": str(MAX_DESIGN_BYTES + 20_000)})
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


# ---- Extracting tokens from a prose-only document (2026-08-20) ----
#
# Extraction itself is covered by test_design_tokens.py. Here, only the route's
# responsibilities: preview does not save, confirmed values are embedded in the raw
# document, the fence wins, and zero tokens do not pass silently.

PROSE_ONLY_MD = "# ACME\n\n주 버튼은 딥 그린 `#00754a`를 쓴다.\n"

REPLY = "```tokens\nprimary: #00754a\nradius: 0.75rem\n```\n"


class FakeExtractor:
    def __init__(self, reply: str = REPLY) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


@pytest.fixture()
def extractor(monkeypatch):
    fake = FakeExtractor()
    monkeypatch.setattr(app_module, "design_token_extractor", lambda: fake)
    return fake


def test_preview_extracts_tokens_without_saving(profiles, client, extractor):
    res = client.post("/admin/design/preview", files={
        "file": ("acme.md", PROSE_ONLY_MD.encode("utf-8"), "text/markdown")})
    assert res.status_code == 200
    body = res.json()
    assert body["origin"] == "extracted"
    assert body["tokens"] == {"primary": "#00754a", "radius": "0.75rem"}
    assert len(extractor.prompts) == 1
    # This is the confirmation step — saving happens only after the admin clicks.
    assert client.get("/admin/design").json() == {"profile": None}


def test_preview_of_a_file_with_a_fence_does_not_call_the_model(
        profiles, client, extractor):
    res = client.post("/admin/design/preview", files={
        "file": ("acme.md", GOOD_MD.encode("utf-8"), "text/markdown")})
    assert res.json()["origin"] == "fence"
    assert res.json()["tokens"] == {"primary": "#5b2ea6"}
    assert extractor.prompts == []


def test_preview_without_a_model_warns_instead_of_failing(
        profiles, client, monkeypatch):
    # Upload must continue even without a model (prose only is applied). Returning 500 here
    # would halt the entire brand feature on deployments without ANTHROPIC_MODEL.
    monkeypatch.setattr(app_module, "design_token_extractor", lambda: None)
    res = client.post("/admin/design/preview", files={
        "file": ("acme.md", PROSE_ONLY_MD.encode("utf-8"), "text/markdown")})
    assert res.status_code == 200
    assert res.json()["origin"] == "none"
    assert res.json()["warnings"]


def test_preview_rejects_a_non_markdown_file(profiles, client, extractor):
    # If preview and PUT gate differently, a file that passed preview gets rejected on save.
    res = client.post("/admin/design/preview", files={
        "file": ("brand.pdf", b"%PDF-1.4", "application/pdf")})
    assert res.status_code == 415


def test_upload_injects_the_confirmed_tokens_into_the_stored_markdown(
        profiles, client):
    res = client.put("/admin/design",
                     files={"file": ("acme.md", PROSE_ONLY_MD.encode("utf-8"),
                                     "text/markdown")},
                     data={"tokens": json.dumps({"primary": "#00754a"})})
    assert res.status_code == 200
    assert res.json()["profile"]["tokens"] == {"primary": "#00754a"}
    # What is stored is still the single raw document — derived values are not stored
    # separately. So when the admin downloads the raw document, they can see that block
    # and fix it by hand next time.
    raw = client.get("/admin/design/raw").text
    assert "```tokens" in raw
    assert "primary: #00754a" in raw
    assert "주 버튼은 딥 그린" in raw


def test_upload_lets_the_files_own_fence_win_over_the_confirmed_tokens(
        profiles, client):
    res = client.put("/admin/design",
                     files={"file": ("acme.md", GOOD_MD.encode("utf-8"),
                                     "text/markdown")},
                     data={"tokens": json.dumps({"primary": "#00754a"})})
    assert res.json()["profile"]["tokens"] == {"primary": "#5b2ea6"}
    assert client.get("/admin/design/raw").text == GOOD_MD


@pytest.mark.parametrize("field", ["보내긴 했는데 JSON이 아니다", "[1, 2]", '"x"'])
def test_upload_rejects_a_malformed_tokens_field(profiles, client, field):
    res = client.put("/admin/design",
                     files={"file": ("acme.md", PROSE_ONLY_MD.encode("utf-8"),
                                     "text/markdown")},
                     data={"tokens": field})
    assert res.status_code == 400


def test_upload_rejects_confirmed_tokens_outside_the_whitelist(profiles, client):
    res = client.put("/admin/design",
                     files={"file": ("acme.md", PROSE_ONLY_MD.encode("utf-8"),
                                     "text/markdown")},
                     data={"tokens": json.dumps({"brand": "#00754a"})})
    assert res.status_code == 400
    assert "brand" in res.json()["detail"]


def test_upload_without_tokens_says_the_brand_will_not_reach_the_screen(
        profiles, client):
    res = client.put("/admin/design", files={
        "file": ("acme.md", PROSE_ONLY_MD.encode("utf-8"), "text/markdown")})
    assert res.status_code == 200
    assert res.json()["profile"]["warnings"] == ["no-tokens"]
    # Reopening must yield the same sentence — because it is derived, not stored.
    assert client.get("/admin/design").json()["profile"]["warnings"] == ["no-tokens"]


def test_upload_with_tokens_carries_no_warning(profiles, client):
    res = client.put("/admin/design", files={
        "file": ("acme.md", GOOD_MD.encode("utf-8"), "text/markdown")})
    assert res.json()["profile"]["warnings"] == []


def test_injection_that_would_cross_the_size_limit_is_rejected(profiles, client):
    # Never create a state where we reject, on re-upload, a file we ourselves saved.
    from aipds.design_profile import MAX_DESIGN_BYTES
    body = ("# ACME\n" + "가" * 10).encode("utf-8")
    body += b"x" * (MAX_DESIGN_BYTES - len(body) - 4)
    assert len(body) < MAX_DESIGN_BYTES
    res = client.put("/admin/design",
                     files={"file": ("acme.md", body, "text/markdown")},
                     data={"tokens": json.dumps({"primary": "#00754a",
                                                 "radius": "0.75rem"})})
    assert res.status_code == 413


def test_pm_is_forbidden(profiles, client):
    me = Principal(username="pm@x", sub="s-pm", role="pm")
    app_module.app.dependency_overrides.pop(require_admin, None)
    app_module.app.dependency_overrides[require_user] = lambda: me
    assert client.get("/admin/design").status_code == 403
