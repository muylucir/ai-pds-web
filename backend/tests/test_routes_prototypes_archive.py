# backend/tests/test_routes_prototypes_archive.py — handoff zip for the dev team.
from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

import aipds.app as app_module
from aipds.proto.host import TOKEN_FILENAME
from aipds.workspace import Workspace
from fakes.fake_runner import FakeRunner
from fakes.in_memory_s3 import FakeS3Store

client = TestClient(app_module.app)

PID = "archive-test"
SLUG = "demo"


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "")
    s3 = FakeS3Store()

    async def fake_make_workspace(pid):
        return Workspace(FakeRunner(FakeS3Store()))

    monkeypatch.setattr(app_module, "make_workspace", fake_make_workspace)
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: s3)
    monkeypatch.setattr(app_module, "_proto_root", lambda: tmp_path)
    client.post("/projects", json={"project_id": PID})
    yield {"s3": s3, "root": tmp_path}
    app_module.registry.remove(PID)


def _names(resp) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        return sorted(zf.namelist())


def test_archive_zips_the_local_build_directory(env):
    build = env["root"] / PID / SLUG
    (build / "prototype").mkdir(parents=True)
    (build / "prototype" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (build / "prototype" / "README.md").write_text("# howto", encoding="utf-8")

    resp = client.get(f"/projects/{PID}/prototypes/{SLUG}/archive")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert _names(resp) == ["prototype/README.md", "prototype/app.js"]


def test_archive_excludes_build_artifacts(env):
    build = env["root"] / PID / SLUG / "prototype"
    build.mkdir(parents=True)
    (build / "app.js").write_text("x", encoding="utf-8")
    for rel in ("node_modules/pkg/index.js", ".next/cache/x.bin", ".git/HEAD"):
        p = build / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("junk", encoding="utf-8")
    (build.parent / ".proto-host.log").write_text("log", encoding="utf-8")
    (build.parent / ".proto-host.pid").write_text("123", encoding="utf-8")

    assert _names(client.get(f"/projects/{PID}/prototypes/{SLUG}/archive")) == \
        ["prototype/app.js"]


def test_archive_never_ships_the_access_token(env):
    """`.proto-token`은 이 프로토타입의 **접근 자격증명**이다.

    빌드 디렉토리(`{root}/{pid}/{slug}`)에 `.proto-host.*`와 나란히 놓이고,
    아카이브는 바로 그 디렉토리를 훑는다 — 제외하지 않으면 "다운로드"를 누른
    모든 사람에게 살아 있는 접근 토큰이 함께 나간다. 그 다운로드를 받는 사람은
    토큰이 막으려는 바로 그 대상일 수 있다.

    위 test_archive_excludes_build_artifacts와 따로 두는 이유는 실패의 의미가
    다르기 때문이다: 저쪽이 깨지면 zip이 커지고, 이쪽이 깨지면 자격증명이 샌다.
    """
    build = env["root"] / PID / SLUG / "prototype"
    build.mkdir(parents=True)
    (build / "app.js").write_text("x", encoding="utf-8")
    # 실제 코드가 쓰는 위치와 이름을 그대로 쓴다(상수를 import해서 — 리터럴을
    # 복사하면 파일명이 바뀔 때 이 테스트가 조용히 무의미해진다).
    (build.parent / TOKEN_FILENAME).write_text("super-secret-token",
                                               encoding="utf-8")

    resp = client.get(f"/projects/{PID}/prototypes/{SLUG}/archive")

    assert _names(resp) == ["prototype/app.js"]
    # 이름뿐 아니라 값이 어디에도 없어야 한다 — 다른 항목에 섞여 들어가는 경로도 막는다.
    assert b"super-secret-token" not in resp.content


def test_archive_excludes_survey_and_transcript_from_the_s3_fallback(env):
    """Survey responses are anonymous respondents' words and the transcript is
    build chatter -- neither belongs in a zip handed to the dev team, and both
    live under the same prototypes/{slug}/ prefix as the bundle."""
    s3 = env["s3"]
    s3.blobs[f"prototypes/{SLUG}/bundle/app.js"] = "console.log(1)"
    s3.blobs[f"prototypes/{SLUG}/survey/responses/r1.json"] = '{"a":"secret"}'
    s3.blobs[f"prototypes/{SLUG}/transcript/main/00000001.jsonl"] = '{"type":"user"}'

    names = _names(client.get(f"/projects/{PID}/prototypes/{SLUG}/archive"))

    assert names == ["app.js"]


def test_archive_preserves_binary_assets(env):
    png = b"\x89PNG\r\n\x1a\n\xff\xfe\xfd"
    build = env["root"] / PID / SLUG / "prototype"
    build.mkdir(parents=True)
    (build / "logo.png").write_bytes(png)

    resp = client.get(f"/projects/{PID}/prototypes/{SLUG}/archive")

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert zf.read("prototype/logo.png") == png


def test_archive_404_when_nothing_built(env):
    assert client.get(f"/projects/{PID}/prototypes/{SLUG}/archive").status_code == 404


def test_archive_content_disposition_survives_non_ascii_slug(env):
    build = env["root"] / PID / "한글-앱"
    build.mkdir(parents=True)
    (build / "app.js").write_text("x", encoding="utf-8")

    resp = client.get(f"/projects/{PID}/prototypes/한글-앱/archive")

    assert resp.status_code == 200
    assert "filename*=UTF-8''" in resp.headers["content-disposition"]
