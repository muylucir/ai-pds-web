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


@pytest.mark.asyncio
async def test_restore_reads_manifests_and_skips_garbage():
    root = FakeS3Store()
    root.blobs["pa/project.json"] = json.dumps({"project_id": "pa", "name": "A"})
    root.blobs["pb/project.json"] = json.dumps({"project_id": "pb", "name": None})
    root.blobs["pc/project.json"] = "{{{ not json"           # 손상 → 건너뜀
    root.blobs["pd/project.json"] = "[1,2,3]"                # JSON but not dict → 건너뜀
    root.blobs["pa/aiplc-docs/audit.md"] = "# not a manifest"  # 매니페스트 아님 → 무시
    restored = dict(await restore_projects(root))
    assert restored == {"pa": "A", "pb": None}


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
