# backend/tests/test_routes_export.py — 프로젝트 번들 내보내기.
from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

import aipds.app as app_module
from aipds.agent.session_store import project_transcript_prefix
from aipds.project_bundle import MANIFEST_NAME
from aipds.proto.host import TOKEN_FILENAME
from aipds.workspace import Workspace
from fakes.fake_runner import FakeRunner
from fakes.in_memory_s3 import FakeS3Store

client = TestClient(app_module.app)

PID = "export-test"
SLUG = "demo"
SPEC_KEY = f"aiplc-docs/discovery/prototypes/{SLUG}/PROTOTYPE-{SLUG}.md"


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")
    s3 = FakeS3Store()

    async def fake_make_workspace(pid):
        return Workspace(FakeRunner(FakeS3Store()))

    monkeypatch.setattr(app_module, "make_workspace", fake_make_workspace)
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: s3)
    monkeypatch.setattr(app_module, "_proto_root", lambda: tmp_path)
    client.post("/projects", json={"project_id": PID, "name": "수출 테스트",
                                   "language": "ko"})
    yield {"s3": s3, "root": tmp_path}
    app_module.registry.remove(PID)
    app_module.proto_sessions.pop((PID, SLUG), None)


def _zip(resp) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(resp.content))


def _names(resp) -> list[str]:
    with _zip(resp) as zf:
        return sorted(zf.namelist())


def _manifest(resp) -> dict:
    with _zip(resp) as zf:
        return json.loads(zf.read(MANIFEST_NAME))


def test_export_carries_artifacts_uploads_answers_and_approvals(env):
    s3 = env["s3"]
    s3.blobs["aiplc-docs/aiplc-state.md"] = "# state"
    s3.blobs["aiplc-docs/audit.md"] = "# audit"
    s3.blobs["uploads/ab12/notes.md"] = "# notes"
    s3.blobs["answers/toolu_1.json"] = '{"answers":{}}'
    s3.blobs["approvals/2026-01-01T00-00-00-abcd1234.json"] = '{"doc_hash":"h"}'

    resp = client.get(f"/projects/{PID}/export")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert _names(resp) == [
        MANIFEST_NAME,
        "project/aiplc-docs/aiplc-state.md",
        "project/aiplc-docs/audit.md",
        "project/answers/toolu_1.json",
        "project/approvals/2026-01-01T00-00-00-abcd1234.json",
        "project/uploads/ab12/notes.md",
    ]


def test_export_strips_the_session_segment_from_the_transcript(env):
    """대화가 다른 프로젝트 id로도 복원될 수 있어야 한다 — 세션 uuid가 경로에
    남아 있으면 그 프로젝트에만 묶인다."""
    prefix = project_transcript_prefix(PID)
    env["s3"].blobs[f"{prefix}main/00000001.jsonl"] = '{"type":"user"}'

    resp = client.get(f"/projects/{PID}/export")

    assert "project/discovery/transcript/main/00000001.jsonl" in _names(resp)
    assert prefix.split("/")[2] not in "".join(_names(resp))


def test_export_omits_pending_and_the_project_manifest(env):
    s3 = env["s3"]
    s3.blobs["project.json"] = '{"project_id":"export-test"}'
    s3.blobs["pending/questions.json"] = '{"interrupt_id":"i1"}'
    s3.blobs["aiplc-docs/audit.md"] = "# audit"

    names = _names(client.get(f"/projects/{PID}/export"))

    assert names == [MANIFEST_NAME, "project/aiplc-docs/audit.md"]


def test_export_carries_survey_but_not_build_chatter(env):
    s3 = env["s3"]
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs[f"prototypes/{SLUG}/survey/questionnaire.json"] = '{"token":"t"}'
    s3.blobs[f"prototypes/{SLUG}/survey/responses/r1.json"] = '{"answers":{}}'
    s3.blobs[f"prototypes/{SLUG}/transcript/s1/main/00000001.jsonl"] = '{"x":1}'

    names = _names(client.get(f"/projects/{PID}/export"))

    assert f"project/prototypes/{SLUG}/survey/questionnaire.json" in names
    assert f"project/prototypes/{SLUG}/survey/responses/r1.json" in names
    assert not any("transcript" in n for n in names)


def test_export_carries_prototype_source_from_local_disk(env):
    """프로토타입 코드는 S3에 없다 — 인프로세스 빌더가 로컬 트리에 직접 쓴다."""
    env["s3"].blobs[SPEC_KEY] = "# spec"
    build = env["root"] / PID / SLUG / "prototype"
    build.mkdir(parents=True)
    (build / "package.json") .write_text('{"name":"p"}', encoding="utf-8")
    (build / "app.tsx").write_text("export default null", encoding="utf-8")

    names = _names(client.get(f"/projects/{PID}/export"))

    assert f"prototypes/{SLUG}/source/prototype/app.tsx" in names
    assert f"prototypes/{SLUG}/source/prototype/package.json" in names


def test_export_never_ships_the_prototype_access_token(env):
    """`.proto-token`은 공개 프리뷰를 막는 자격증명이다. 번들을 받는 사람이
    그 토큰이 막으려는 대상일 수 있다."""
    env["s3"].blobs[SPEC_KEY] = "# spec"
    build = env["root"] / PID / SLUG
    (build / "prototype").mkdir(parents=True)
    (build / "prototype" / "app.tsx").write_text("x", encoding="utf-8")
    (build / TOKEN_FILENAME).write_text("super-secret-token", encoding="utf-8")

    resp = client.get(f"/projects/{PID}/export")

    assert not any(TOKEN_FILENAME in n for n in _names(resp))
    assert b"super-secret-token" not in resp.content


def test_export_excludes_build_artifacts(env):
    env["s3"].blobs[SPEC_KEY] = "# spec"
    build = env["root"] / PID / SLUG / "prototype"
    build.mkdir(parents=True)
    (build / "app.tsx").write_text("x", encoding="utf-8")
    for rel in ("node_modules/pkg/i.js", ".next/cache/x", ".git/HEAD"):
        p = build / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("junk", encoding="utf-8")

    names = _names(client.get(f"/projects/{PID}/export"))

    assert [n for n in names if n.startswith("prototypes/")] == \
        [f"prototypes/{SLUG}/source/prototype/app.tsx"]


def test_export_preserves_binary_assets(env):
    png = b"\x89PNG\r\n\x1a\n\xff\xfe\xfd"
    env["s3"].blobs[SPEC_KEY] = "# spec"
    build = env["root"] / PID / SLUG / "prototype"
    build.mkdir(parents=True)
    (build / "logo.png").write_bytes(png)

    with _zip(client.get(f"/projects/{PID}/export")) as zf:
        assert zf.read(f"prototypes/{SLUG}/source/prototype/logo.png") == png


def test_manifest_describes_the_project_and_its_prototypes(env):
    s3 = env["s3"]
    s3.blobs[SPEC_KEY] = "# spec"
    s3.blobs["aiplc-docs/audit.md"] = "# audit"
    s3.blobs[f"prototypes/{SLUG}/survey/questionnaire.json"] = '{"token":"t"}'
    s3.blobs[f"prototypes/{SLUG}/survey/responses/r1.json"] = '{"answers":{}}'

    manifest = _manifest(client.get(f"/projects/{PID}/export"))

    assert manifest["source_project_id"] == PID
    assert manifest["project"]["name"] == "수출 테스트"
    assert manifest["project"]["language"] == "ko"
    assert manifest["prototypes"] == [
        {"slug": SLUG, "source_files": 0,
         "survey": {"exists": True, "responses": 1}},
    ]


def test_manifest_counts_what_it_left_behind(env):
    """제외가 조용하면 '설문이 없던 프로젝트'와 '설문을 빠뜨린 번들'이 같아 보인다."""
    s3 = env["s3"]
    s3.blobs["aiplc-docs/audit.md"] = "# audit"
    s3.blobs["pending/questions.json"] = "{}"

    counts = _manifest(client.get(f"/projects/{PID}/export"))["counts"]

    assert counts["objects"] == 1
    assert counts["objects_excluded"] == 1


def test_export_409_while_a_build_session_is_running(env):
    """에이전트가 쓰고 있는 트리를 반쯤 담은 zip은 정직한 스냅샷이 아니다."""
    env["s3"].blobs[SPEC_KEY] = "# spec"

    class _Live:
        status = "building"

    app_module.proto_sessions[(PID, SLUG)] = _Live()

    resp = client.get(f"/projects/{PID}/export")

    assert resp.status_code == 409
    assert resp.json()["detail"] == "build_session_active"


def test_export_404_for_an_unknown_project(env):
    assert client.get("/projects/nope/export").status_code == 404


def test_export_404_for_a_traversal_pid(env):
    assert client.get("/projects/%2e%2e/export").status_code == 404


def test_content_disposition_survives_a_non_ascii_pid(env, monkeypatch):
    pid = "한글-프로젝트"
    client.post("/projects", json={"project_id": pid})
    try:
        resp = client.get(f"/projects/{pid}/export")

        assert resp.status_code == 200
        assert "filename*=UTF-8''" in resp.headers["content-disposition"]
    finally:
        app_module.registry.remove(pid)
