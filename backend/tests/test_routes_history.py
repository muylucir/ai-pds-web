import json
from fastapi.testclient import TestClient
import aipds.app as app_module
from aipds.workspace import Workspace
from tests.fakes.in_memory_s3 import FakeS3Store
from tests.fakes.fake_runner import FakeRunner

client = TestClient(app_module.app)

def _local_project(monkeypatch, pid):
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")  # offline: no durable manifest write
    async def make(project_id):
        return Workspace(FakeRunner())
    monkeypatch.setattr(app_module, "make_workspace", make)
    client.post("/projects", json={"project_id": pid})

def test_history_empty_when_no_session(monkeypatch):
    _local_project(monkeypatch, "h2")
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: FakeS3Store())
    assert client.get("/projects/h2/history").json() == {"items": []}

def test_history_unknown_project_404(monkeypatch):
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: FakeS3Store())
    assert client.get("/projects/ghost/history").status_code == 404

def test_history_degrades_when_factory_raises(monkeypatch):
    _local_project(monkeypatch, "h3")
    def boom(pid):
        raise RuntimeError("aws profile broken")
    monkeypatch.setattr(app_module, "s3_store_factory", boom)
    r = client.get("/projects/h3/history")
    assert r.status_code == 200 and r.json() == {"items": []}


# ---- Whether the claude driver (current default) path restores through the route ----
#
# The actual shape of this bug was here: the driver writes to the store (projects/{pid}/) it
# got via s3_store_factory(pid), but the route only read session_s3_factory() (sessions/). The
# prefixes differed, so it was always an empty list, with no error. So this test is only
# meaningful as a round trip **through the route**, not as a unit test.

async def _write_cli_transcript(s3, project_id, entries):
    """Write with the key the driver actually uses.

    `project_id` must not be used as the session key as is — that was the second layer of this
    bug. The CLI rejects a non-UUID session-id, so the driver derives a uuid5 via
    `_sdk_session_id` and mirrors with that value. If this helper wrote with the original
    project_id and the route read with the same value, the tests alone would pass even with
    the write key and read key out of sync (and that is in fact how they were passing).
    """
    from aipds.agent.claude_driver import _sdk_session_id
    from aipds.agent.session_store import DiscoverySessionStore
    session_id, _ = _sdk_session_id({"session_id": project_id})
    await DiscoverySessionStore(s3).append({"session_id": session_id}, entries)


def test_history_restores_a_claude_driver_transcript(monkeypatch):
    import asyncio
    _local_project(monkeypatch, "h5")
    project_s3 = FakeS3Store()
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        _write_cli_transcript(project_s3, "h5", [
            {"type": "queue-operation", "operation": "enqueue"},   # bookkeeping line
            {"type": "user", "message": {"role": "user", "content": "시작해줘"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "네, 시작합니다."}]}},
        ]))
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: project_s3)
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: FakeS3Store())
    items = client.get("/projects/h5/history").json()["items"]
    assert [(i["role"], i["text"]) for i in items] == [
        ("user", "시작해줘"), ("ai", "네, 시작합니다.")]


def test_history_degrades_when_the_project_store_raises(monkeypatch):
    """A store creation failure (credentials or bucket not configured) empties the history but
    does not block the screen. Previously it fell over to the strands fallback store, but that
    driver was deleted, so there is only one place to read — on failure, an empty list is the
    only honest answer."""
    _local_project(monkeypatch, "h7")

    def boom(pid):
        raise RuntimeError("aws profile broken")

    monkeypatch.setattr(app_module, "s3_store_factory", boom)
    r = client.get("/projects/h7/history")
    assert r.status_code == 200 and r.json() == {"items": []}


# ---- GET /context: the last turn's context usage ----

def test_context_is_the_last_turns_record(monkeypatch):
    from aipds.context_usage import DISCOVERY_KEY
    _local_project(monkeypatch, "c1")
    s3 = FakeS3Store()
    s3.blobs[DISCOVERY_KEY] = json.dumps({"left_pct": 62, "total_tokens": 285000,
                                          "max_tokens": 750000, "compact_at_tokens": 717000})
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: s3)

    assert client.get("/projects/c1/context").json()["context"]["left_pct"] == 62


def test_context_is_null_before_any_turn(monkeypatch):
    _local_project(monkeypatch, "c2")
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: FakeS3Store())
    assert client.get("/projects/c2/context").json() == {"context": None}


def test_context_degrades_when_factory_raises(monkeypatch):
    _local_project(monkeypatch, "c3")
    def boom(pid):
        raise RuntimeError("aws profile broken")
    monkeypatch.setattr(app_module, "s3_store_factory", boom)
    r = client.get("/projects/c3/context")
    assert r.status_code == 200 and r.json() == {"context": None}
