import json
from fastapi.testclient import TestClient
import pathfinder.app as app_module
from pathfinder.workspace import Workspace
from tests.fakes.in_memory_s3 import FakeS3Store
from tests.fakes.fake_runner import FakeRunner

client = TestClient(app_module.app)

def _local_project(monkeypatch, pid):
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "")  # offline: no durable manifest write
    async def make(project_id):
        return Workspace(FakeRunner())
    monkeypatch.setattr(app_module, "make_workspace", make)
    client.post("/projects", json={"project_id": pid})

def test_history_returns_items_from_session_store(monkeypatch):
    _local_project(monkeypatch, "h1")
    s3 = FakeS3Store()
    s3.blobs["session_h1/agents/agent_default/messages/message_0.json"] = json.dumps(
        {"message": {"role": "user", "content": [{"text": "안녕"}]}, "message_id": 0})
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: s3)
    body = client.get("/projects/h1/history").json()
    # answers/questions는 답변 제출 턴과 질문 카드에만 채워진다 — 보통
    # 말풍선에서는 둘 다 None이다.
    assert body == {"items": [{"role": "user", "text": "안녕", "card": None,
                               "name": None, "trace": [], "answers": None,
                               "questions": None}]}

def test_history_empty_when_no_session(monkeypatch):
    _local_project(monkeypatch, "h2")
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: FakeS3Store())
    assert client.get("/projects/h2/history").json() == {"items": []}

def test_history_unknown_project_404(monkeypatch):
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: FakeS3Store())
    assert client.get("/projects/ghost/history").status_code == 404

def test_history_degrades_when_factory_raises(monkeypatch):
    _local_project(monkeypatch, "h3")
    def boom():
        raise RuntimeError("aws profile broken")
    monkeypatch.setattr(app_module, "session_s3_factory", boom)
    r = client.get("/projects/h3/history")
    assert r.status_code == 200 and r.json() == {"items": []}


# ---- claude 드라이버(현재 기본) 경로가 라우트를 통해 복원되는가 ----
#
# 이 버그의 실제 모양이 여기에 있었다: 드라이버는 s3_store_factory(pid)로 받은
# 스토어(projects/{pid}/)에 쓰는데 라우트는 session_s3_factory()(sessions/)만
# 읽었다. 프리픽스가 달라 항상 빈 목록이었고 에러도 없었다. 그래서 이 테스트는
# 단위가 아니라 **라우트를 통해** 왕복해야 의미가 있다.

async def _write_cli_transcript(s3, project_id, entries):
    """드라이버가 실제로 쓰는 키로 쓴다.

    `project_id`를 그대로 세션 키로 쓰면 안 된다 — 그것이 이 버그의 두 번째
    겹이었다. CLI는 비-UUID session-id를 거부하므로 드라이버는
    `_sdk_session_id`로 uuid5를 유도해 그 값으로 미러링한다. 이 헬퍼가 원본
    project_id로 쓰고 라우트가 같은 값으로 읽으면, 쓰는 키와 읽는 키가 어긋난
    상태에서도 테스트만 통과한다(실제로 그렇게 통과하고 있었다).
    """
    from pathfinder.agent.claude_driver import _sdk_session_id
    from pathfinder.agent.session_store import DiscoverySessionStore
    session_id, _ = _sdk_session_id({"session_id": project_id})
    await DiscoverySessionStore(s3).append({"session_id": session_id}, entries)


def test_history_restores_a_claude_driver_transcript(monkeypatch):
    import asyncio
    _local_project(monkeypatch, "h5")
    project_s3 = FakeS3Store()
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        _write_cli_transcript(project_s3, "h5", [
            {"type": "queue-operation", "operation": "enqueue"},   # 부기 줄
            {"type": "user", "message": {"role": "user", "content": "시작해줘"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "네, 시작합니다."}]}},
        ]))
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: project_s3)
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: FakeS3Store())
    items = client.get("/projects/h5/history").json()["items"]
    assert [(i["role"], i["text"]) for i in items] == [
        ("user", "시작해줘"), ("ai", "네, 시작합니다.")]


def test_history_still_restores_strands_when_the_claude_path_is_empty(monkeypatch):
    # 드라이버를 되돌렸거나 교체 전 세션 — 폴백이 살아 있어야 한다.
    _local_project(monkeypatch, "h6")
    session_s3 = FakeS3Store()
    session_s3.blobs["session_h6/agents/agent_default/messages/message_0.json"] = \
        json.dumps({"message": {"role": "user", "content": [{"text": "예전 대화"}]},
                    "message_id": 0})
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: FakeS3Store())
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: session_s3)
    items = client.get("/projects/h6/history").json()["items"]
    assert [(i["role"], i["text"]) for i in items] == [("user", "예전 대화")]


def test_history_degrades_when_the_project_store_raises(monkeypatch):
    # 한쪽 스토어 생성 실패가 다른 쪽 복원을 막지 않는다.
    _local_project(monkeypatch, "h7")
    def boom(pid):
        raise RuntimeError("aws profile broken")
    session_s3 = FakeS3Store()
    session_s3.blobs["session_h7/agents/agent_default/messages/message_0.json"] = \
        json.dumps({"message": {"role": "user", "content": [{"text": "폴백"}]},
                    "message_id": 0})
    monkeypatch.setattr(app_module, "s3_store_factory", boom)
    monkeypatch.setattr(app_module, "session_s3_factory", lambda: session_s3)
    items = client.get("/projects/h7/history").json()["items"]
    assert [(i["role"], i["text"]) for i in items] == [("user", "폴백")]
