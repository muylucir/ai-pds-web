# backend/tests/test_routes_answers.py
from pathlib import Path
import asyncio
from fastapi.testclient import TestClient
import aipds.app as app_module
from aipds.app import app, registry
from aipds.workspace import Workspace
from fakes.fake_runner import FakeRunner

FIX = Path(__file__).parent / "fixtures"
client = TestClient(app)

def _seed(monkeypatch, pid, language=None):
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")  # offline: no durable manifest write
    async def make(project_id):
        return Workspace(FakeRunner())
    monkeypatch.setattr(app_module, "make_workspace", make)
    body = {"project_id": pid}
    if language is not None:
        body["language"] = language
    client.post("/projects", json=body)
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


# ---- 상태 파일이 없을 때의 지목 ----
# 스테이지 배지는 `aiplc-state.md`에서 유도된다(agent/reconcile.py). 훅과 턴 경계
# 재조정은 파일이 **있을 때** 그것을 화면으로 옮기고, 파일 자체가 없으면 옮길 것이
# 없다 — 그리고 그것을 만들 수 있는 것은 에이전트뿐이다(스테이지 이름을 아는 것이
# 에이전트뿐이므로, 경로에서 추측하면 룰셋 스테이지 이름의 두 번째 사본이 생긴다).
#
# 답변 제출 턴이 그 지목의 자리다. 2026-08-18 test123456에서 첫 턴이 상태 파일 없이
# 끝났고, 재개 턴은 "멈춘 지점부터"라서 스스로 그것을 만들 계기가 없었다.

def _state(pid: str, markdown: str):
    asyncio.run(registry.get(pid).runner.write_file(
        "aiplc-docs/aiplc-state.md", markdown))


def _resume_text(pid: str) -> str:
    handle = _submit(pid, {"1": "B"}).json()["turn_id"]
    payload = app_module.turn_handles.consume(pid, handle)
    assert payload is not None
    return payload["text"]


def test_the_resume_turn_asks_for_the_state_file_when_it_never_landed(
        monkeypatch):
    _seed(monkeypatch, "fq5")  # aiplc-state.md 없음
    text = _resume_text("fq5")
    assert "aiplc-state.md" in text
    # 파일 지목은 그대로 남아야 한다 — 노트가 원래 프롬프트를 대체하지 않는다.
    assert "strategy-questions.md" in text


def test_a_state_file_without_a_current_stage_still_counts_as_missing(monkeypatch):
    """판정은 파일 부재가 아니라 `current_stage is None`이다.

    상류 룰이 요구하는 형태에는 항상 Current Stage 줄이 있으므로, 그 줄이 없는
    파일은 읽을 상태가 없다는 뜻이다 — 파일 존재만 보면 이 경우를 놓친다."""
    _seed(monkeypatch, "fq6")
    _state("fq6", "# AI-PLC State\n\n## Stage Progress\n")
    assert "aiplc-state.md" in _resume_text("fq6")


def test_the_resume_turn_stays_quiet_once_a_stage_is_recorded(monkeypatch):
    """정상 순서로 돈 턴에는 이 노트가 붙지 않는다.

    매 라운드 붙으면 에이전트가 이미 선언한 스테이지를 다시 선언하고, 그 반복이
    체크리스트를 흔든다."""
    _seed(monkeypatch, "fq7")
    _state("fq7", "# AI-PLC State\n\n- **Current Stage**: Envision\n\n"
                  "## Stage Progress\n- [ ] Envision\n")
    text = _resume_text("fq7")
    assert "aiplc-state.md" not in text
    assert "strategy-questions.md" in text


def test_the_note_follows_the_project_language(monkeypatch):
    """에이전트가 읽는 문장이므로 UI 언어가 아니라 프로젝트 언어다
    (agent/prompts.py 헤더의 규약)."""
    _seed(monkeypatch, "fq8", language="en")
    text = _resume_text("fq8")
    assert "aiplc-state.md" in text
    # 영어 프로젝트의 프롬프트에 한글이 섞이면 그 프로젝트의 대화가 한국어로
    # 샌다 — 2026-08-04 결함의 모양이다.
    assert not any("가" <= ch <= "힣" for ch in text)


def test_a_failing_state_probe_does_not_break_answer_submission(monkeypatch):
    """사용자는 폼을 이미 제출했다. 노트는 보조 지시이므로 제출을 막지 못한다."""
    _seed(monkeypatch, "fq9")
    async def boom():
        raise RuntimeError("s3 down")
    monkeypatch.setattr(registry.get("fq9"), "get_state", boom)
    r = _submit("fq9", {"1": "B"})
    assert r.status_code == 200, r.text
    assert r.json()["turn_id"]
