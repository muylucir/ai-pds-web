# backend/tests/test_routes_design.py
#
# 라우트 계층의 책임만: 업로드 검증의 HTTP 번역, 응답 모양, 관리자 게이트.
# 파싱 자체는 test_design_profile.py가 본다.
from __future__ import annotations

import json

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


def test_spoofed_content_length_rejected_before_body_read(profiles, client):
    # Reviewer-flagged gap(test_routes_uploads.py의 짝): 위의
    # test_oversized_upload_is_rejected는 본문 자체가 한계를 넘어서 사전 체크
    # 없이도 재검사(len(data))만으로 413이 나온다 — 사전 체크 분기가 한 번도
    # 실행되지 않은 채 "존재"만 했을 수 있다. 여기서는 본문은 작게 두고
    # Content-Length 헤더만 위조해서, 본문을 읽기도 전에 사전 체크 단독으로
    # 413이 나오는지를 확인한다(TestClient/httpx는 명시적 Content-Length
    # 오버라이드를 그대로 보낸다).
    from pathfinder.design_profile import MAX_DESIGN_BYTES
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


# ---- 산문뿐인 문서에서 토큰을 뽑는다(2026-08-20) ----
#
# 추출 자체는 test_design_tokens.py가 본다. 여기서는 라우트의 책임만:
# preview는 저장하지 않는다, 확인된 값이 원문에 박힌다, 펜스가 이긴다,
# 0토큰이 조용히 지나가지 않는다.

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
    # 확인 단계다 — 저장은 관리자가 누른 다음이다.
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
    # 모델 없이도 업로드는 계속돼야 한다(산문만 적용). 여기서 500을 내면
    # ANTHROPIC_MODEL이 없는 배포에서 브랜드 기능 전체가 멈춘다.
    monkeypatch.setattr(app_module, "design_token_extractor", lambda: None)
    res = client.post("/admin/design/preview", files={
        "file": ("acme.md", PROSE_ONLY_MD.encode("utf-8"), "text/markdown")})
    assert res.status_code == 200
    assert res.json()["origin"] == "none"
    assert res.json()["warnings"]


def test_preview_rejects_a_non_markdown_file(profiles, client, extractor):
    # preview와 PUT의 관문이 갈리면 preview를 통과한 파일이 저장에서 거부된다.
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
    # 저장물은 여전히 원문 하나다 — 파생값을 따로 담지 않는다. 그래서 원문을
    # 내려받으면 관리자가 그 블록을 눈으로 보고 다음번엔 손으로 고칠 수 있다.
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
    # 다시 열어도 같은 문장이 나와야 한다 — 저장하지 않고 유도하기 때문이다.
    assert client.get("/admin/design").json()["profile"]["warnings"] == ["no-tokens"]


def test_upload_with_tokens_carries_no_warning(profiles, client):
    res = client.put("/admin/design", files={
        "file": ("acme.md", GOOD_MD.encode("utf-8"), "text/markdown")})
    assert res.json()["profile"]["warnings"] == []


def test_injection_that_would_cross_the_size_limit_is_rejected(profiles, client):
    # 우리가 저장한 파일을 우리가 재업로드에서 거부하는 상태를 만들지 않는다.
    from pathfinder.design_profile import MAX_DESIGN_BYTES
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
