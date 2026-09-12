# backend/tests/test_routes_import.py — 번들을 프로젝트로 되살린다.
from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

import aipds.app as app_module
from aipds.agent.session_store import project_transcript_prefix
from aipds.import_staging import MAX_IMPORT_BYTES
from aipds.project_bundle import MANIFEST_NAME, build_manifest
from aipds.project_import import (
    WARN_MODEL_UNAVAILABLE,
    WARN_SURVEY_TOKENS_REISSUED,
    WARN_UNKNOWN_ENTRIES,
)
from aipds.survey.store import TOKEN_INDEX_PREFIX
from aipds.workspace import Workspace
from fakes.fake_import_staging import FakeImportStaging
from fakes.fake_runner import FakeRunner
from fakes.in_memory_s3 import FakeS3Store

client = TestClient(app_module.app)

SOURCE_PID = "origin-proj"
SLUG = "demo"
SEED_MODEL = "global.anthropic.claude-sonnet-5"


def _bundle(entries: dict[str, bytes] | None = None, **manifest_over) -> bytes:
    """번들 zip 하나. `manifest_over`로 매니페스트 필드를 덮어쓴다."""
    manifest = json.loads(build_manifest(
        exported_at="2026-09-12T00:00:00+00:00",
        source_project_id=SOURCE_PID,
        project={"name": "원본 프로젝트", "created_at": "2026-08-01T00:00:00+00:00",
                 "model_id": SEED_MODEL, "language": "en"},
        counts={}, prototypes=[]))
    manifest.update(manifest_over)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False))
        for name, body in (entries or {}).items():
            zf.writestr(name, body)
    return buf.getvalue()


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")
    stores: dict[str, FakeS3Store] = {}
    root = FakeS3Store()          # projects/ 매니페스트
    surveys = FakeS3Store()       # 버킷 루트(토큰 인덱스)
    staging = FakeImportStaging()

    async def fake_make_workspace(pid):
        return Workspace(FakeRunner(FakeS3Store()))

    monkeypatch.setattr(app_module, "make_workspace", fake_make_workspace)
    monkeypatch.setattr(app_module, "s3_store_factory",
                        lambda pid: stores.setdefault(pid, FakeS3Store()))
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    monkeypatch.setattr(app_module, "surveys_root_s3_factory", lambda: surveys)
    monkeypatch.setattr(app_module, "import_staging", lambda: staging)
    monkeypatch.setattr(app_module, "_proto_root", lambda: tmp_path)
    created: list[str] = []

    def _track(pid: str) -> str:
        created.append(pid)
        return pid

    yield {"stores": stores, "root": root, "surveys": surveys,
           "staging": staging, "proto_root": tmp_path, "track": _track}
    for pid in {SOURCE_PID, *created}:
        app_module.registry.remove(pid)


def _upload(env, body: bytes, upload_id: str = "a" * 32) -> str:
    env["staging"].objects[upload_id] = body
    return upload_id


def _import(env, body: bytes, project_id: str | None = None,
            upload_id: str = "a" * 32):
    uid = _upload(env, body, upload_id)
    payload: dict = {"upload_id": uid}
    if project_id is not None:
        payload["project_id"] = project_id
    return client.post("/project-imports", json=payload)


# ---- presign ----


def test_upload_request_returns_a_presigned_url(env):
    resp = client.post("/project-imports/uploads", json={"size_bytes": 1024})

    assert resp.status_code == 201
    body = resp.json()
    assert body["url"].startswith("https://")
    assert body["content_type"] == "application/zip"
    assert body["max_bytes"] == MAX_IMPORT_BYTES
    assert env["staging"].presigned == [body["upload_id"]]


def test_upload_request_refuses_an_oversized_bundle_before_it_starts(env):
    resp = client.post("/project-imports/uploads",
                       json={"size_bytes": MAX_IMPORT_BYTES + 1})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "export_too_large"
    assert env["staging"].presigned == []


def test_upload_request_needs_a_positive_size(env):
    assert client.post("/project-imports/uploads",
                       json={"size_bytes": 0}).status_code == 400


def test_import_is_unavailable_without_a_bucket(env, monkeypatch):
    """조용히 강등하면 사용자는 수백 MB를 올린 뒤에 그 사실을 알게 된다."""
    def _no_bucket():
        raise RuntimeError("project import requires AIPDS_S3_BUCKET")

    monkeypatch.setattr(app_module, "import_staging", _no_bucket)

    resp = client.post("/project-imports/uploads", json={"size_bytes": 10})

    assert resp.status_code == 503
    assert resp.json()["detail"] == "import_unavailable"


# ---- 기본 경로 ----


def test_import_registers_the_project_under_the_bundles_own_id(env):
    resp = _import(env, _bundle({"project/aiplc-docs/audit.md": b"# audit"}))

    assert resp.status_code == 201
    body = resp.json()
    assert body["project_id"] == SOURCE_PID
    assert body["source_project_id"] == SOURCE_PID
    assert app_module.registry.is_registered(SOURCE_PID)
    assert env["stores"][SOURCE_PID].blobs["aiplc-docs/audit.md"] == "# audit"


def test_import_preserves_name_created_at_and_language(env):
    """생성일을 원본 그대로 둔다 — '이 프로젝트는 8월에 시작됐다'가 그 프로젝트의
    사실이고, 목록 정렬은 그 사실을 따른다."""
    resp = _import(env, _bundle())

    assert resp.json()["name"] == "원본 프로젝트"
    assert resp.json()["language"] == "en"
    assert app_module.registry.get_created_at(SOURCE_PID) == \
        "2026-08-01T00:00:00+00:00"
    manifest = json.loads(env["root"].blobs[f"{SOURCE_PID}/project.json"])
    assert manifest["created_at"] == "2026-08-01T00:00:00+00:00"
    assert manifest["language"] == "en"


def test_manifest_is_written_and_the_project_survives_a_restart(env):
    """project.json이 '이 프로젝트가 존재한다'의 정본이다(restore_projects)."""
    _import(env, _bundle())

    assert f"{SOURCE_PID}/project.json" in env["root"].blobs


def test_import_under_a_new_id_moves_the_transcript_with_it(env):
    """트랜스크립트 키의 uuid5는 프로젝트 id에서 유도된다 — 재작성하지 않으면
    히스토리를 읽는 쪽이 빈 prefix를 보고, 그 실패는 에러 없이 조용하다."""
    target = env["track"]("copied-proj")
    body = _bundle({
        "project/discovery/transcript/main/00000001.jsonl": b'{"type":"user"}',
        "project/discovery/transcript/sub/agent-a/00000001.jsonl": b'{"type":"x"}',
    })

    resp = _import(env, body, project_id=target)

    assert resp.status_code == 201
    keys = set(env["stores"][target].blobs.keys())
    prefix = project_transcript_prefix(target)
    assert f"{prefix}main/00000001.jsonl" in keys
    assert f"{prefix}sub/agent-a/00000001.jsonl" in keys
    # 원본 프로젝트의 세션 prefix로는 아무것도 쓰이지 않았다.
    assert not any(k.startswith(project_transcript_prefix(SOURCE_PID))
                   for k in keys)


def test_import_lands_prototype_source_on_local_disk(env):
    """카드가 built로 보이려면 {proto_root}/{pid}/{slug}/prototype/ 이 비어 있지
    않아야 한다(proto/session.has_build_output)."""
    target = env["track"]("with-proto")
    body = _bundle({
        f"prototypes/{SLUG}/source/prototype/app.tsx": b"export default null",
        f"prototypes/{SLUG}/source/prototype/package.json": b'{"name":"p"}',
    })

    resp = _import(env, body, project_id=target)

    assert resp.status_code == 201
    built = env["proto_root"] / target / SLUG / "prototype"
    assert (built / "app.tsx").read_bytes() == b"export default null"
    assert (built / "package.json").exists()
    assert resp.json()["counts"]["prototypes"] == 1


def test_import_deletes_the_staged_bundle_on_success(env):
    uid = _upload(env, _bundle())

    client.post("/project-imports", json={"upload_id": uid})

    assert env["staging"].objects == {}


# ---- 설문 토큰 ----


def _survey_bundle(token: str = "original-token") -> bytes:
    definition = json.dumps({"token": token, "status": "open", "slug": SLUG,
                             "project_id": SOURCE_PID, "created_at": "c",
                             "title": "t", "hypothesis": "h",
                             "questions": [{"id": "q1"}]}).encode("utf-8")
    return _bundle({
        f"project/prototypes/{SLUG}/survey/questionnaire.json": definition,
        f"project/prototypes/{SLUG}/survey/responses/r1.json": b'{"answers":{}}',
    })


def test_survey_token_is_reissued_and_indexed_to_the_new_project(env):
    """원본 토큰을 그대로 쓰면 같은 버킷에서 원본 프로젝트의 살아 있는 설문
    링크가 새 프로젝트로 넘어간다 — 응답이 엉뚱한 곳에 쌓이고 에러는 없다."""
    target = env["track"]("survey-copy")

    resp = _import(env, _survey_bundle(), project_id=target)

    assert resp.status_code == 201
    key = f"prototypes/{SLUG}/survey/questionnaire.json"
    definition = json.loads(env["stores"][target].blobs[key])
    assert definition["token"] != "original-token"
    assert definition["project_id"] == target
    index = json.loads(
        env["surveys"].blobs[f"{TOKEN_INDEX_PREFIX}{definition['token']}.json"])
    assert index == {"project_id": target, "slug": SLUG}
    # 원본 토큰의 인덱스는 만들지 않는다.
    assert f"{TOKEN_INDEX_PREFIX}original-token.json" not in env["surveys"].blobs
    assert {"code": WARN_SURVEY_TOKENS_REISSUED, "count": 1} in \
        resp.json()["warnings"]


def test_survey_responses_come_along(env):
    target = env["track"]("survey-answers")

    _import(env, _survey_bundle(), project_id=target)

    assert env["stores"][target].blobs[
        f"prototypes/{SLUG}/survey/responses/r1.json"] == '{"answers":{}}'


def test_archived_survey_token_changes_but_gets_no_live_index(env):
    """지난 회차의 링크가 살아 있을 이유는 없지만, 원본 토큰을 남기면 그것이
    원본 인스턴스의 링크와 같은 값이 된다."""
    target = env["track"]("survey-archive")
    archived = json.dumps({"token": "old-archived", "status": "closed",
                           "slug": SLUG, "project_id": SOURCE_PID,
                           "created_at": "c", "title": "t", "hypothesis": "h",
                           "questions": [{"id": "q1"}]}).encode("utf-8")
    body = _bundle({
        f"project/prototypes/{SLUG}/survey/archive/2026-01-01/questionnaire.json":
            archived,
    })

    _import(env, body, project_id=target)

    key = f"prototypes/{SLUG}/survey/archive/2026-01-01/questionnaire.json"
    definition = json.loads(env["stores"][target].blobs[key])
    assert definition["token"] != "old-archived"
    assert definition["project_id"] == target
    assert env["surveys"].blobs.keys() == set()


def test_unreadable_questionnaire_fails_the_whole_import(env):
    """정의를 읽을 수 없으면 토큰을 바꿀 수도 없다 — 그대로 두면 원본의 토큰이
    살아남는다."""
    target = env["track"]("bad-survey")
    body = _bundle({
        f"project/prototypes/{SLUG}/survey/questionnaire.json": b"not json",
    })

    resp = _import(env, body, project_id=target)

    assert resp.status_code == 400
    assert not app_module.registry.is_registered(target)
    assert env["stores"][target].blobs.keys() == set()


# ---- 모델 폴백 ----


def test_a_model_this_instance_cannot_select_is_dropped_with_a_warning(env):
    """조용히 강등하면 프로젝트가 '고른 적 없는 모델'로 돈다 — 이 기능에서 가장
    조용한 실패다."""
    target = env["track"]("foreign-model")
    body = _bundle(project={"name": "n", "created_at": "c",
                            "model_id": "some.other.model", "language": "ko"})

    resp = _import(env, body, project_id=target)

    assert resp.status_code == 201
    assert resp.json()["model_id"] is None
    assert {"code": WARN_MODEL_UNAVAILABLE, "count": 1} in resp.json()["warnings"]
    assert app_module.registry.get_model_id(target) is None


def test_a_selectable_model_is_kept_without_a_warning(env):
    resp = _import(env, _bundle())

    assert resp.json()["model_id"] == SEED_MODEL
    assert resp.json()["warnings"] == []


# ---- 충돌 ----


def test_an_existing_id_is_409_and_keeps_the_upload(env):
    """id 충돌은 매니페스트를 읽어야 알 수 있다 — 여기서 스테이징을 지우면
    사용자는 id 하나 바꾸려고 번들 전체를 다시 올려야 한다."""
    client.post("/projects", json={"project_id": SOURCE_PID})
    uid = _upload(env, _bundle())

    resp = client.post("/project-imports", json={"upload_id": uid})

    assert resp.status_code == 409
    assert resp.json()["detail"] == "project_exists"
    assert uid in env["staging"].objects


def test_retrying_a_409_with_a_new_id_needs_no_re_upload(env):
    client.post("/projects", json={"project_id": SOURCE_PID})
    target = env["track"]("second-copy")
    uid = _upload(env, _bundle({"project/aiplc-docs/audit.md": b"# audit"}))
    assert client.post("/project-imports",
                       json={"upload_id": uid}).status_code == 409

    resp = client.post("/project-imports",
                       json={"upload_id": uid, "project_id": target})

    assert resp.status_code == 201
    assert env["stores"][target].blobs["aiplc-docs/audit.md"] == "# audit"


# ---- 거절 ----


def test_a_missing_upload_is_reported_as_such(env):
    """사용자가 할 일이 '다른 파일을 고르는 것'이 아니라 '다시 올리는 것'이다."""
    resp = client.post("/project-imports", json={"upload_id": "b" * 32})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "upload_missing"


@pytest.mark.parametrize("upload_id", [
    "../projects/victim/project", "a" * 31, "A" * 32, "", "not-hex-" + "0" * 24,
])
def test_a_forged_upload_id_is_refused(env, upload_id):
    """이 값이 S3 키가 된다 — 모양을 강제하는 것이 클라이언트가 키를 정할 여지를
    남기지 않는다."""
    resp = client.post("/project-imports", json={"upload_id": upload_id})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "export_invalid"


def test_a_non_zip_upload_is_refused_and_discarded(env):
    uid = _upload(env, b"this is not a zip")

    resp = client.post("/project-imports", json={"upload_id": uid})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "export_invalid"
    assert env["staging"].objects == {}


def test_a_zip_without_a_manifest_is_refused(env):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("project/aiplc-docs/audit.md", "# audit")

    resp = _import(env, buf.getvalue())

    assert resp.status_code == 400
    assert resp.json()["detail"] == "export_invalid"


def test_another_kind_of_aipds_zip_is_refused(env):
    """산출물 아카이브나 프로토타입 핸드오프 zip을 임포트 칸에 떨어뜨리는 것은
    흔한 실수다."""
    resp = _import(env, _bundle(kind="aipds-artifacts"))

    assert resp.status_code == 400


def test_a_newer_schema_version_is_refused(env):
    """모르는 버전을 최선으로 해석하면 절반만 들어온 프로젝트가 되고, 그 상태는
    화면에서 정상과 구별되지 않는다."""
    resp = _import(env, _bundle(schema_version=99))

    assert resp.status_code == 400


def test_a_traversal_project_id_is_refused(env):
    resp = _import(env, _bundle(), project_id="../evil")

    assert resp.status_code == 400
    assert resp.json()["detail"] == "export_invalid"


def test_a_traversal_entry_is_refused_and_nothing_is_written(env):
    target = env["track"]("zip-slip")
    body = _bundle({"project/aiplc-docs/ok.md": b"# ok"})
    # zipfile은 우리가 넣은 이름을 그대로 저장한다 — 공격자의 zip을 흉내낸다.
    buf = io.BytesIO(body)
    with zipfile.ZipFile(buf, "a") as zf:
        zf.writestr("project/../../../../etc/passwd", "root:x:0:0")

    resp = _import(env, buf.getvalue(), project_id=target)

    assert resp.status_code == 400
    assert not app_module.registry.is_registered(target)
    assert env["stores"][target].blobs.keys() == set()


def test_an_oversized_upload_is_refused_before_download(env, monkeypatch):
    monkeypatch.setattr("aipds.routes.transfer.MAX_IMPORT_BYTES", 10)
    uid = _upload(env, _bundle())

    resp = client.post("/project-imports", json={"upload_id": uid})

    assert resp.status_code == 413
    assert resp.json()["detail"] == "export_too_large"
    assert env["staging"].objects == {}


def test_unrecognised_entries_are_skipped_but_reported(env):
    """사용자가 zip을 다시 압축하며 붙은 `__MACOSX/` 같은 것이 흔하다. 건너뛰되
    조용히 넘기지는 않는다."""
    target = env["track"]("extra-entries")
    body = _bundle({
        "project/aiplc-docs/audit.md": b"# audit",
        "__MACOSX/._audit.md": b"junk",
        "readme.txt": b"hello",
    })

    resp = _import(env, body, project_id=target)

    assert resp.status_code == 201
    assert {"code": WARN_UNKNOWN_ENTRIES, "count": 2} in resp.json()["warnings"]
    assert env["stores"][target].blobs.keys() == {"aiplc-docs/audit.md"}


def test_a_manifest_write_failure_rolls_the_import_back(env, monkeypatch):
    """매니페스트가 없으면 재시작 후 사라지는 프로젝트다 — 그런 것을 조용히
    만들지 않는다(생성 라우트와 같은 판단)."""
    target = env["track"]("rollback-proj")

    class _Boom(FakeS3Store):
        async def put(self, key, content):
            raise RuntimeError("s3 down")

    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: _Boom())
    body = _bundle({
        "project/aiplc-docs/audit.md": b"# audit",
        f"prototypes/{SLUG}/source/prototype/app.tsx": b"x",
        f"project/prototypes/{SLUG}/survey/questionnaire.json": json.dumps(
            {"token": "t", "slug": SLUG, "project_id": SOURCE_PID}).encode(),
    })

    resp = _import(env, body, project_id=target)

    assert resp.status_code == 500
    assert not app_module.registry.is_registered(target)
    # 쓴 것이 전부 되돌려졌다 — S3 객체, 토큰 인덱스, 로컬 소스 트리.
    assert env["stores"][target].blobs.keys() == set()
    assert env["surveys"].blobs.keys() == set()
    assert not (env["proto_root"] / target).exists()
