# backend/tests/test_routes_answers.py
from pathlib import Path
import asyncio
from fastapi.testclient import TestClient
import pathfinder.app as app_module
from pathfinder.app import app, registry
from pathfinder.workspace import Workspace
from fakes.fake_runner import FakeRunner

FIX = Path(__file__).parent / "fixtures"
client = TestClient(app)

def _seed(monkeypatch, pid):
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "")  # offline: no durable manifest write
    async def make(project_id):
        return Workspace(FakeRunner())
    monkeypatch.setattr(app_module, "make_workspace", make)
    client.post("/projects", json={"project_id": pid})
    ws = registry.get(pid)
    # Use asyncio.run (not get_event_loop().run_until_complete) — the latter is
    # deprecated on 3.11 and conflicts with pytest-asyncio's managed loop.
    asyncio.run(
        ws.runner.write_file("aiplc-docs/strategy-questions.md",
            (FIX / "strategy-questions.md").read_text(encoding="utf-8")))

def test_put_answers_updates_file(monkeypatch):
    _seed(monkeypatch, "ans1")
    r = client.put("/projects/ans1/questions/aiplc-docs/strategy-questions.md",
                   json={"answers": {"1": "B", "12": "A,C"}})
    assert r.status_code == 200
    by_num = {q["number"]: q["answer"] for q in r.json()["questions"]}
    assert by_num[1] == "B"
    assert by_num[12] == "A,C"

def test_put_unknown_question_400(monkeypatch):
    _seed(monkeypatch, "ans2")
    r = client.put("/projects/ans2/questions/aiplc-docs/strategy-questions.md",
                   json={"answers": {"99": "A"}})
    assert r.status_code == 400

def test_put_non_numeric_key_400(monkeypatch):
    _seed(monkeypatch, "ans3")
    r = client.put("/projects/ans3/questions/aiplc-docs/strategy-questions.md",
                   json={"answers": {"abc": "A"}})
    assert r.status_code == 400


# ---- 파일 질문 라운드의 답변 제출 ----
# 이 경로는 파킹된 턴으로 돌아가지 않는다. PostToolUse 훅이 질문 파일을 보고 턴을
# **끝냈으므로**(claude_driver._on_post_tool_use) 이어갈 턴이 없다 — 답변을 파일에
# 쓰고 **새 턴**으로 에이전트를 다시 부른다. 그 새 턴의 텍스트는 백엔드가 만든다:
# 에이전트가 읽는 문장이므로 UI 언어가 아니라 프로젝트 언어를 따라야 하고
# (agent/prompts.py의 규율), 프론트에 그 문구를 두면 두 언어를 프론트가 관리하게 된다.

def _submit(pid: str, answers: dict):
    return client.post(
        f"/projects/{pid}/questions/aiplc-docs/strategy-questions.md/answers",
        json={"answers": answers})


def test_submitting_file_answers_writes_them_and_returns_a_turn_handle(monkeypatch):
    _seed(monkeypatch, "fq1")
    r = _submit("fq1", {"1": "B"})
    assert r.status_code == 200, r.text
    body = r.json()
    # 파일에 기록됐다 — 번호로 쓰므로 퍼지 매칭이 없다(serialize_answers).
    by_num = {q["number"]: q["answer"] for q in body["questions"]["questions"]}
    assert by_num[1] == "B"
    # 그리고 이어갈 턴의 핸들을 돌려준다. 프론트는 기존 GET /events?turn=로 연다 —
    # 새 스트림 엔드포인트를 만들지 않는다.
    assert body["turn_id"]


def test_the_handle_carries_a_prompt_that_names_the_file(monkeypatch):
    """핸들에 담긴 텍스트가 에이전트에게 갈 문장이다.

    파일을 지목하지 않으면 에이전트가 어느 파일을 되읽어야 하는지 모른다 —
    질문 파일이 여러 개 있는 것이 정상이다(실측: 한 프로젝트에 9개)."""
    _seed(monkeypatch, "fq2")
    handle = _submit("fq2", {"1": "B"}).json()["turn_id"]
    payload = app_module.turn_handles.consume("fq2", handle)
    assert payload is not None
    assert "strategy-questions.md" in payload["text"]


def test_submitting_to_a_missing_file_404(monkeypatch):
    _seed(monkeypatch, "fq3")
    r = client.post("/projects/fq3/questions/aiplc-docs/nope.md/answers",
                    json={"answers": {"1": "B"}})
    assert r.status_code == 404


def test_submitting_an_unknown_question_number_400(monkeypatch):
    """PUT과 같은 계약이다 — 조용히 무시하면 답변이 사라진 것을 아무도 모른다."""
    _seed(monkeypatch, "fq4")
    assert _submit("fq4", {"99": "A"}).status_code == 400
    assert _submit("fq4", {"abc": "A"}).status_code == 400
