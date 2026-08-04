import json
import pytest
from pathfinder.project_store import write_manifest, restore_projects, delete_project_data
from tests.fakes.in_memory_s3 import FakeS3Store


@pytest.mark.asyncio
async def test_write_manifest_puts_expected_key_and_shape():
    root = FakeS3Store()
    await write_manifest(root, "p1", "이름")
    d = json.loads(root.blobs["p1/project.json"])
    assert d["project_id"] == "p1" and d["name"] == "이름"
    assert d["created_at"].endswith("+00:00") or d["created_at"].endswith("Z")  # UTC ISO8601
    # 모델 미지정은 명시적 null로 기록한다 — 키 자체를 빼면 "구 매니페스트"와
    # "모델을 고르지 않은 새 프로젝트"를 구별할 수 없다.
    assert d["model_id"] is None


@pytest.mark.asyncio
async def test_write_manifest_records_the_model_id():
    root = FakeS3Store()
    await write_manifest(root, "p1", None,
                         model_id="global.anthropic.claude-opus-5")
    d = json.loads(root.blobs["p1/project.json"])
    assert d["model_id"] == "global.anthropic.claude-opus-5"


@pytest.mark.asyncio
async def test_restore_reads_manifests_and_skips_garbage():
    root = FakeS3Store()
    root.blobs["pa/project.json"] = json.dumps(
        {"project_id": "pa", "name": "A", "created_at": "2026-07-22T01:00:00+00:00",
         "model_id": "global.anthropic.claude-opus-5", "language": "en"})
    root.blobs["pb/project.json"] = json.dumps({"project_id": "pb", "name": None})
    root.blobs["pc/project.json"] = "{{{ not json"           # 손상 → 건너뜀
    root.blobs["pd/project.json"] = "[1,2,3]"                # JSON but not dict → 건너뜀
    root.blobs["pa/aiplc-docs/audit.md"] = "# not a manifest"  # 매니페스트 아님 → 무시
    restored = {pid: (name, created_at, model_id, language)
                for pid, name, created_at, model_id, language
                in await restore_projects(root)}
    # created_at·model_id·language는 매니페스트에서 승계, 없으면(구 매니페스트) None.
    assert restored == {
        "pa": ("A", "2026-07-22T01:00:00+00:00",
               "global.anthropic.claude-opus-5", "en"),
        "pb": (None, None, None, None),
    }


@pytest.mark.asyncio
async def test_write_manifest_records_the_language():
    root = FakeS3Store()
    await write_manifest(root, "p1", None, language="en")
    assert json.loads(root.blobs["p1/project.json"])["language"] == "en"


@pytest.mark.asyncio
async def test_write_manifest_records_an_unset_language_as_explicit_null():
    # 키를 빼면 '구 매니페스트'와 '언어를 고르지 않은 새 프로젝트'를 구별할 수
    # 없다 — model_id와 같은 판단이다.
    root = FakeS3Store()
    await write_manifest(root, "p1", None)
    assert json.loads(root.blobs["p1/project.json"])["language"] is None


@pytest.mark.asyncio
async def test_restore_empty_store_returns_empty():
    assert await restore_projects(FakeS3Store()) == []


@pytest.mark.asyncio
async def test_delete_project_data_removes_both_prefixes():
    sessions, root = FakeS3Store(), FakeS3Store()
    sessions.blobs["session_p1/agents/agent_default/messages/message_0.json"] = "{}"
    sessions.blobs["session_p2/agents/agent_default/messages/message_0.json"] = "{}"
    root.blobs["p1/project.json"] = "{}"
    root.blobs["p1/aiplc-docs/audit.md"] = "x"
    root.blobs["p2/project.json"] = "{}"
    await delete_project_data(sessions, root, "p1")
    assert list(sessions.blobs) == ["session_p2/agents/agent_default/messages/message_0.json"]
    assert list(root.blobs) == ["p2/project.json"]
