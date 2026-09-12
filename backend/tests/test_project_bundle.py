# backend/tests/test_project_bundle.py — 프로젝트 번들 포맷.
from __future__ import annotations

import json

import pytest

from aipds.agent.session_store import project_transcript_prefix
from aipds.project_bundle import (
    KIND,
    MAX_ENTRIES,
    SCHEMA_VERSION,
    BundleError,
    build_manifest,
    bundle_path_to_s3_key,
    parse_manifest,
    parse_proto_source_path,
    proto_source_path,
    s3_key_to_bundle_path,
    safe_entry_name,
    source_excluded,
)
from aipds.proto.host import TOKEN_FILENAME

PID = "bundle-fmt"


@pytest.fixture
def tp() -> str:
    return project_transcript_prefix(PID)


# ---- S3 키 ↔ 번들 경로 ----


def test_ordinary_keys_land_under_project(tp):
    assert s3_key_to_bundle_path("aiplc-docs/audit.md", transcript_prefix=tp) == \
        "project/aiplc-docs/audit.md"
    assert s3_key_to_bundle_path("uploads/ab/x.md", transcript_prefix=tp) == \
        "project/uploads/ab/x.md"
    assert s3_key_to_bundle_path("approvals/2026-01-01.json", transcript_prefix=tp) == \
        "project/approvals/2026-01-01.json"


def test_project_manifest_is_not_copied(tp):
    """같은 사실이 export.json에도 있어야 하므로(임포트가 프로젝트를 만들기 전에
    읽는다) 두 곳에 두지 않는다."""
    assert s3_key_to_bundle_path("project.json", transcript_prefix=tp) is None


def test_pending_is_excluded(tp):
    assert s3_key_to_bundle_path("pending/questions.json", transcript_prefix=tp) is None
    assert s3_key_to_bundle_path("pending/question-file.json",
                                 transcript_prefix=tp) is None


def test_prototype_build_transcript_is_excluded(tp):
    assert s3_key_to_bundle_path("prototypes/demo/transcript/abc/main/00000001.jsonl",
                                 transcript_prefix=tp) is None
    # 설문은 남는다 — 같은 prototypes/{slug}/ prefix를 공유하지만 옮기는 대상이다.
    assert s3_key_to_bundle_path("prototypes/demo/survey/questionnaire.json",
                                 transcript_prefix=tp) == \
        "project/prototypes/demo/survey/questionnaire.json"


def test_discovery_transcript_loses_its_session_segment(tp):
    key = f"{tp}main/00000007.jsonl"

    path = s3_key_to_bundle_path(key, transcript_prefix=tp)

    assert path == "project/discovery/transcript/main/00000007.jsonl"
    # 세션 uuid가 경로에 남아 있으면 다른 id로 임포트할 때 그대로 따라간다.
    assert tp.split("/")[2] not in path


def test_subagent_transcript_keeps_its_subpath(tp):
    key = f"{tp}sub/agent-x/00000002.jsonl"

    assert s3_key_to_bundle_path(key, transcript_prefix=tp) == \
        "project/discovery/transcript/sub/agent-x/00000002.jsonl"


def test_transcript_of_another_session_is_dropped(tp):
    """활성 세션이 아닌 트랜스크립트는 히스토리가 읽지 않는 죽은 데이터이고,
    세션 세그먼트를 벗기면 배치 번호가 활성 세션과 충돌해 한쪽이 덮어써진다."""
    other = "discovery/transcript/11111111-2222-3333-4444-555555555555/main/00000001.jsonl"

    assert s3_key_to_bundle_path(other, transcript_prefix=tp) is None


def test_round_trip_restores_the_target_projects_session(tp):
    """다른 프로젝트로 임포트하면 트랜스크립트가 **그 프로젝트의** 세션 prefix로
    돌아가야 한다. 이 왕복이 깨지면 대화가 에러 없이 사라진다."""
    target = project_transcript_prefix("some-other-project")
    assert target != tp

    path = s3_key_to_bundle_path(f"{tp}main/00000003.jsonl", transcript_prefix=tp)
    back = bundle_path_to_s3_key(path, transcript_prefix=target)

    assert back == f"{target}main/00000003.jsonl"


def test_ordinary_keys_round_trip_unchanged(tp):
    for key in ("aiplc-docs/discovery/envision/business-context.md",
                "answers/toolu_123.json",
                "prototypes/demo/survey/responses/r1.json"):
        path = s3_key_to_bundle_path(key, transcript_prefix=tp)
        assert bundle_path_to_s3_key(path, transcript_prefix=tp) == key


def test_paths_outside_project_dir_are_not_s3_keys(tp):
    assert bundle_path_to_s3_key("export.json", transcript_prefix=tp) is None
    assert bundle_path_to_s3_key("prototypes/demo/source/app.js",
                                 transcript_prefix=tp) is None
    assert bundle_path_to_s3_key("project/", transcript_prefix=tp) is None


# ---- 프로토타입 소스 경로 ----


def test_proto_source_path_round_trip():
    path = proto_source_path("demo", "prototype/app/page.tsx")

    assert path == "prototypes/demo/source/prototype/app/page.tsx"
    assert parse_proto_source_path(path) == ("demo", "prototype/app/page.tsx")


def test_parse_proto_source_rejects_other_shapes():
    for path in ("prototypes/demo/app.js",          # source/ 가 없다
                 "prototypes/demo/source/",         # 파일이 없다
                 "prototypes//source/app.js",       # slug 가 없다
                 "project/aiplc-docs/audit.md"):
        assert parse_proto_source_path(path) is None


# ---- 제외 규칙 ----


def test_source_excluded_covers_build_artifacts_and_bookkeeping():
    assert source_excluded("node_modules/pkg/index.js")
    assert source_excluded("prototype/.next/cache/x")
    assert source_excluded(".git/HEAD")
    assert source_excluded(".proto-host.log")
    assert source_excluded(".proto-host.pid")
    assert not source_excluded("prototype/app/page.tsx")
    assert not source_excluded("prototype/package.json")


def test_source_excluded_never_ships_the_access_token():
    """`.proto-token`은 프리뷰를 막는 **자격증명**이다. 위 테스트와 따로 두는
    이유는 실패의 의미가 다르기 때문이다 — 저쪽이 깨지면 zip이 커지고, 이쪽이
    깨지면 자격증명이 샌다."""
    assert source_excluded(TOKEN_FILENAME)
    assert source_excluded(f"prototype/{TOKEN_FILENAME}")


# ---- zip 엔트리 안전성 ----


def test_safe_entry_name_accepts_ordinary_paths():
    assert safe_entry_name("project/aiplc-docs/audit.md") == \
        "project/aiplc-docs/audit.md"
    assert safe_entry_name("프로젝트/문서.md") == "프로젝트/문서.md"


@pytest.mark.parametrize("name", [
    "/etc/passwd",                      # 절대경로
    "../../etc/passwd",                 # 상위 탈출
    "project/../../../etc/passwd",      # 중간 탈출
    "..\\..\\windows\\system32",        # 백슬래시 탈출
    "C:/Windows/system.ini",            # 드라이브 문자
    "project/",                         # 디렉터리 엔트리
    "",                                 # 빈 이름
])
def test_safe_entry_name_rejects_traversal(name):
    with pytest.raises(BundleError):
        safe_entry_name(name)


def test_entry_cap_is_a_real_number():
    """상한이 없으면 압축 폭탄이 먼저 죽이는 것은 프로세스가 아니라 인스턴스의
    디스크이고, 그 디스크에는 다른 프로젝트의 빌드 트리가 함께 산다."""
    assert MAX_ENTRIES > 0


# ---- 매니페스트 ----


def _manifest(**over) -> bytes:
    raw = build_manifest(
        exported_at="2026-09-12T00:00:00+00:00",
        source_project_id=PID,
        project={"name": "n", "created_at": "c", "model_id": None, "language": "ko"},
        counts={"artifacts": 1},
        prototypes=[],
    )
    data = json.loads(raw)
    data.update(over)
    return json.dumps(data).encode("utf-8")


def test_manifest_round_trip():
    data = parse_manifest(_manifest())

    assert data["kind"] == KIND
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["source_project_id"] == PID
    assert data["project"]["language"] == "ko"


def test_manifest_documents_what_was_left_out():
    """받는 쪽이 '왜 이건 안 왔지'를 파일 하나로 답할 수 있어야 한다."""
    excluded = parse_manifest(_manifest())["excluded"]

    assert "node_modules" in excluded
    assert TOKEN_FILENAME in excluded
    assert "pending/" in excluded
    assert "prototypes/*/transcript/" in excluded


def test_wrong_kind_is_reported_before_version():
    """엉뚱한 zip을 떨어뜨린 사용자에게 '지원하지 않는 버전'은 고칠 수 없는 말이다."""
    with pytest.raises(BundleError, match="not an AI-PDS project bundle"):
        parse_manifest(_manifest(kind="aipds-artifacts", schema_version=999))


def test_unsupported_version_is_refused():
    with pytest.raises(BundleError, match="schema_version"):
        parse_manifest(_manifest(schema_version=SCHEMA_VERSION + 1))


@pytest.mark.parametrize("raw", [b"not json", b"[]", b"\xff\xfe"])
def test_malformed_manifest_is_a_bundle_error(raw):
    with pytest.raises(BundleError):
        parse_manifest(raw)


def test_manifest_without_project_metadata_is_refused():
    with pytest.raises(BundleError, match="project metadata"):
        parse_manifest(_manifest(project=None))


def test_manifest_without_source_id_is_refused():
    with pytest.raises(BundleError, match="source_project_id"):
        parse_manifest(_manifest(source_project_id=""))
