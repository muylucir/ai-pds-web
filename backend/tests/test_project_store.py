import json
import pytest
from aipds.project_store import write_manifest, restore_projects, delete_project_data
from tests.fakes.in_memory_s3 import FakeS3Store


@pytest.mark.asyncio
async def test_write_manifest_puts_expected_key_and_shape():
    root = FakeS3Store()
    await write_manifest(root, "p1", "이름")
    d = json.loads(root.blobs["p1/project.json"])
    assert d["project_id"] == "p1" and d["name"] == "이름"
    assert d["created_at"].endswith("+00:00") or d["created_at"].endswith("Z")  # UTC ISO8601
    # An unspecified model is recorded as an explicit null — omitting the key itself would make it impossible to
    # tell an "old manifest" from "a new project where no model was chosen".
    assert d["model_id"] is None


@pytest.mark.asyncio
async def test_write_manifest_records_the_model_id():
    root = FakeS3Store()
    await write_manifest(root, "p1", None,
                         model_id="global.anthropic.claude-opus-5")
    d = json.loads(root.blobs["p1/project.json"])
    assert d["model_id"] == "global.anthropic.claude-opus-5"


@pytest.mark.asyncio
async def test_write_manifest_records_the_effort_and_null_when_unset():
    """effort is copied as a pair with the model. Unspecified is an explicit null, not an omitted key."""
    root = FakeS3Store()
    await write_manifest(root, "p1", None, model_id="global.anthropic.claude-opus-5-5",
                         effort="medium")
    await write_manifest(root, "p2", None)
    assert json.loads(root.blobs["p1/project.json"])["effort"] == "medium"
    d2 = json.loads(root.blobs["p2/project.json"])
    assert "effort" in d2 and d2["effort"] is None


@pytest.mark.asyncio
async def test_restore_reads_manifests_and_skips_garbage():
    root = FakeS3Store()
    root.blobs["pa/project.json"] = json.dumps(
        {"project_id": "pa", "name": "A", "created_at": "2026-07-22T01:00:00+00:00",
         "model_id": "global.anthropic.claude-opus-5", "language": "en",
         "effort": "high"})
    root.blobs["pb/project.json"] = json.dumps({"project_id": "pb", "name": None})
    root.blobs["pc/project.json"] = "{{{ not json"           # corrupted → skipped
    root.blobs["pd/project.json"] = "[1,2,3]"                # JSON but not dict → skipped
    root.blobs["pa/aiplc-docs/audit.md"] = "# not a manifest"  # not a manifest → ignored
    restored = {p.project_id: (p.name, p.created_at, p.model_id, p.language, p.effort)
                for p in await restore_projects(root)}
    # created_at, model_id, language, and effort are inherited from the manifest; None if absent (old manifest).
    assert restored == {
        "pa": ("A", "2026-07-22T01:00:00+00:00",
               "global.anthropic.claude-opus-5", "en", "high"),
        "pb": (None, None, None, None, None),
    }


@pytest.mark.asyncio
async def test_write_manifest_records_the_language():
    root = FakeS3Store()
    await write_manifest(root, "p1", None, language="en")
    assert json.loads(root.blobs["p1/project.json"])["language"] == "en"


@pytest.mark.asyncio
async def test_write_manifest_records_an_unset_language_as_explicit_null():
    # Omitting the key would make it impossible to tell an 'old manifest' from 'a new project where no language was
    # chosen' — the same judgment as model_id.
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
