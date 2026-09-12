# backend/tests/test_project_transfer_round_trip.py
#
# **이 기능의 핵심 계약.** 내보낸 프로젝트를 다른 id로 되살렸을 때 화면이 같은
# 것을 말해야 한다 — 대화, 산출물, 승인 게이트, 프로토타입 상태, 설문 결과.
#
# 조각별 테스트(test_routes_export/import)로는 이것이 보장되지 않는다: 양쪽이 같은
# 잘못된 규칙을 공유하면 둘 다 초록이다. 특히 트랜스크립트의 세션 세그먼트가
# 그렇다 — export가 벗기고 import가 다시 끼우는 그 왕복이 어긋나면 대화가 **에러
# 없이** 사라진다(list_history가 모든 실패를 []로 강등한다).
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import aipds.app as app_module
from aipds.agent.session_store import project_transcript_prefix
from aipds.survey.store import TOKEN_INDEX_PREFIX
from aipds.workspace import Workspace
from fakes.fake_import_staging import FakeImportStaging
from fakes.fake_runner import FakeRunner
from fakes.in_memory_s3 import FakeS3Store

client = TestClient(app_module.app)

SOURCE = "rt-source"
TARGET = "rt-target"
SLUG = "demo"
SPEC_KEY = f"aiplc-docs/discovery/prototypes/{SLUG}/PROTOTYPE-{SLUG}.md"
SURVEY_KEY = f"prototypes/{SLUG}/survey/questionnaire.json"


class _IdleHost:
    """돌고 있는 호스팅이 없는 상태. 목록 라우트가 host에게 묻는 것은 둘뿐이다."""

    def status(self, pid, slug):
        return None

    def token_for(self, pid, slug):
        return None


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")
    stores: dict[str, FakeS3Store] = {}
    root = FakeS3Store()
    surveys = FakeS3Store()
    staging = FakeImportStaging()

    async def fake_make_workspace(pid):
        return Workspace(FakeRunner(stores.setdefault(pid, FakeS3Store())))

    monkeypatch.setattr(app_module, "make_workspace", fake_make_workspace)
    monkeypatch.setattr(app_module, "s3_store_factory",
                        lambda pid: stores.setdefault(pid, FakeS3Store()))
    monkeypatch.setattr(app_module, "projects_root_s3_factory", lambda: root)
    monkeypatch.setattr(app_module, "surveys_root_s3_factory", lambda: surveys)
    monkeypatch.setattr(app_module, "import_staging", lambda: staging)
    monkeypatch.setattr(app_module, "_proto_root", lambda: tmp_path)
    monkeypatch.setattr(app_module, "proto_host", lambda: _IdleHost())

    client.post("/projects", json={"project_id": SOURCE, "name": "왕복 테스트",
                                   "language": "ko"})
    _seed(stores[SOURCE], tmp_path)
    yield {"stores": stores, "surveys": surveys, "staging": staging,
           "proto_root": tmp_path}
    app_module.registry.remove(SOURCE)
    app_module.registry.remove(TARGET)


#: 실제 CLI 트랜스크립트 모양(Anthropic Messages). session_history가 읽는 형식이다.
_TRANSCRIPT = [
    {"message": {"role": "user", "content": "기획전 어시스턴트를 만들고 싶어"}},
    {"message": {"role": "assistant",
                 "content": [{"type": "text", "text": "어떤 고객을 대상으로 하나요?"}]}},
    {"type": "ai-title", "title": "부기 줄 — 대화가 아니다"},
    {"message": {"role": "user", "content": "20대 여성"}},
    {"message": {"role": "assistant",
                 "content": [{"type": "tool_use", "name": "Write", "id": "t1",
                              "input": {"file_path": "aiplc-docs/x.md"}},
                             {"type": "text", "text": "정리했습니다"}]}},
]


def _seed(s3: FakeS3Store, proto_root) -> None:
    s3.blobs["aiplc-docs/aiplc-state.md"] = (
        "# 상태\n\n| 단계 | 상태 |\n| --- | --- |\n| Envision | completed |\n")
    s3.blobs["aiplc-docs/discovery/discovery-document.md"] = "# 발견 문서\n\n본문"
    s3.blobs["aiplc-docs/audit.md"] = "# 감사\n\n- 1. 시작"
    # ko 프로젝트의 실제 명세 모양 — 라벨이 번역돼 있다(parsers/proto_spec.py).
    s3.blobs[SPEC_KEY] = "# PROTOTYPE demo\n\n- **제품**: 기획전 도우미\n"
    s3.blobs["uploads/ab12/notes.md"] = "# 참고자료"
    s3.blobs["answers/t1.json"] = json.dumps(
        {"answers": {"1": "A"}, "questions": None}, ensure_ascii=False)
    s3.blobs["approvals/2026-08-02T00-00-00-abcd1234.json"] = json.dumps(
        {"document": "aiplc-docs/discovery/discovery-document.md",
         "doc_hash": "deadbeef", "approved_at": "2026-08-02T00:00:00+00:00"})
    s3.blobs[SURVEY_KEY] = json.dumps(
        {"token": "live-token", "status": "open", "slug": SLUG,
         "project_id": SOURCE, "created_at": "2026-08-03T00:00:00+00:00",
         "language": "ko", "title": "검증", "hypothesis": "가설",
         "questions": [{"id": "q1", "type": "scale", "text": "쓸만한가요?",
                        "required": True}]}, ensure_ascii=False)
    s3.blobs[f"prototypes/{SLUG}/survey/responses/r1.json"] = json.dumps(
        {"response_id": "r1", "submitted_at": "2026-08-04T00:00:00+00:00",
         "answers": {"q1": 4}})
    # 대화. 키는 이 프로젝트의 세션 prefix 아래에 있다.
    prefix = project_transcript_prefix(SOURCE)
    s3.blobs[f"{prefix}main/00000001.jsonl"] = "\n".join(
        json.dumps(line, ensure_ascii=False) for line in _TRANSCRIPT)
    # 프로토타입 소스는 로컬 디스크에만 있다.
    build = proto_root / SOURCE / SLUG / "prototype"
    build.mkdir(parents=True)
    (build / "package.json").write_text('{"name":"demo"}', encoding="utf-8")
    (build / "app" / "page.tsx").parent.mkdir(parents=True)
    (build / "app" / "page.tsx").write_text("export default () => null",
                                            encoding="utf-8")
    (build / "public" / "logo.png").parent.mkdir(parents=True)
    (build / "public" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")


def _round_trip(env, target: str = TARGET) -> dict:
    """내보내고 → 스테이징에 올리고 → 다른 id로 가져온다."""
    exported = client.get(f"/projects/{SOURCE}/export")
    assert exported.status_code == 200, exported.text
    uid = "c" * 32
    env["staging"].objects[uid] = exported.content
    resp = client.post("/project-imports",
                       json={"upload_id": uid, "project_id": target})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_the_conversation_survives_a_new_project_id(env):
    """세션 세그먼트 왕복이 어긋나면 히스토리는 빈 목록이 되고, 그 실패에는
    에러가 없다."""
    before = client.get(f"/projects/{SOURCE}/history").json()["items"]
    assert before, "원본 히스토리가 비어 있으면 이 테스트는 아무것도 증명하지 않는다"

    _round_trip(env)

    after = client.get(f"/projects/{TARGET}/history").json()["items"]
    assert after == before


def test_artifacts_and_documents_come_across(env):
    _round_trip(env)

    assert client.get(f"/projects/{TARGET}/document").json()["markdown"] == \
        "# 발견 문서\n\n본문"
    audit = client.get(f"/projects/{TARGET}/files/aiplc-docs/audit.md")
    assert audit.json()["content"] == "# 감사\n\n- 1. 시작"


def test_the_stage_timeline_resumes_where_it_left_off(env):
    before = client.get(f"/projects/{SOURCE}/state").json()

    _round_trip(env)

    assert client.get(f"/projects/{TARGET}/state").json() == before


def test_the_approval_gate_sees_the_same_evidence(env):
    """승인 시점의 문서 해시가 함께 와야 '재승인 필요' 판정이 원본과 같다."""
    before = client.get(f"/projects/{SOURCE}/approvals").json()

    _round_trip(env)

    assert client.get(f"/projects/{TARGET}/approvals").json() == before


def test_the_prototype_card_reports_built_and_keeps_its_name(env):
    """소스가 로컬 트리에 놓였으면 카드가 built다 — 그 상태에서 호스팅 버튼이
    npm 수명주기를 돌려 node_modules를 채운다."""
    _round_trip(env)

    cards = client.get(f"/projects/{TARGET}/prototypes").json()["prototypes"]

    assert [(c["slug"], c["state"], c["name"]) for c in cards] == \
        [(SLUG, "built", "기획전 도우미")]


def test_prototype_source_lands_byte_for_byte(env):
    _round_trip(env)

    src = env["proto_root"] / SOURCE / SLUG / "prototype"
    dst = env["proto_root"] / TARGET / SLUG / "prototype"
    for rel in ("package.json", "app/page.tsx", "public/logo.png"):
        assert (dst / rel).read_bytes() == (src / rel).read_bytes()


def test_the_survey_keeps_its_answers_and_gets_a_live_link(env):
    before = client.get(f"/projects/{SOURCE}/prototypes").json()["prototypes"][0]

    _round_trip(env)

    after = client.get(f"/projects/{TARGET}/prototypes").json()["prototypes"][0]
    assert after["response_count"] == before["response_count"] == 1
    assert after["has_survey"] is True
    definition = json.loads(env["stores"][TARGET].blobs[SURVEY_KEY])
    index = json.loads(
        env["surveys"].blobs[f"{TOKEN_INDEX_PREFIX}{definition['token']}.json"])
    assert index == {"project_id": TARGET, "slug": SLUG}


def test_the_source_project_is_left_exactly_as_it_was(env):
    """복제가 원본을 건드리면 워크숍 중 한 번의 실수가 두 프로젝트를 망친다."""
    before = dict(env["stores"][SOURCE]._raw)
    surveys_before = dict(env["surveys"]._raw)

    _round_trip(env)

    assert env["stores"][SOURCE]._raw == before
    assert surveys_before.keys() <= env["surveys"]._raw.keys()
    # 원본의 설문 토큰은 그대로다 — 이미 배포된 링크가 살아 있어야 한다.
    assert json.loads(env["stores"][SOURCE].blobs[SURVEY_KEY])["token"] == \
        "live-token"


def test_importing_the_same_bundle_twice_yields_two_projects(env):
    """워크숍에서 흔한 조작이다 — 같은 출발점에서 두 갈래를 시작한다."""
    _round_trip(env, TARGET)
    exported = client.get(f"/projects/{SOURCE}/export")
    env["staging"].objects["d" * 32] = exported.content

    second = client.post("/project-imports",
                         json={"upload_id": "d" * 32, "project_id": "rt-third"})
    try:
        assert second.status_code == 201
        first_token = json.loads(env["stores"][TARGET].blobs[SURVEY_KEY])["token"]
        second_token = json.loads(env["stores"]["rt-third"].blobs[SURVEY_KEY])["token"]
        # 두 사본이 같은 토큰을 쓰면 뒤에 임포트한 쪽이 앞의 링크를 가로챈다.
        assert first_token != second_token
        assert client.get("/projects/rt-third/history").json()["items"] == \
            client.get(f"/projects/{TARGET}/history").json()["items"]
    finally:
        app_module.registry.remove("rt-third")


def test_the_imported_project_appears_in_the_list_with_its_metadata(env):
    result = _round_trip(env)

    assert result["name"] == "왕복 테스트"
    detail = client.get(f"/projects/{TARGET}").json()
    assert detail["name"] == "왕복 테스트"
    assert detail["language"] == "ko"
    assert detail["created_at"] == \
        app_module.registry.get_created_at(SOURCE)
