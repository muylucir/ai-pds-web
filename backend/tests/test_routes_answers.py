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
    #
    # **"본문에 한글 없음"으로 보지 않는다(2026-08-21).** 이 텍스트는 이제 질문 파일의
    # 문장을 인용한다(aipds/answer_summary.py). 그 문장은 프로젝트의 콘텐츠이지 우리
    # 프롬프트가 아니고, 실제 영어 프로젝트의 질문 파일은 영어다 — 이 픽스처만
    # 한국어다. 그래서 우리가 만드는 **wrapper**가 프로젝트 언어인지를 본다.
    # 프롬프트 전수 검사는 tests/test_agent_language.py가 담당한다.
    for korean_wrapper in ("질문에 답했습니다", "다시 묻지 마세요",
                           "파일을 읽고 멈춘 지점부터"):
        assert korean_wrapper not in text, korean_wrapper
    assert "I answered the questions" in text


def test_a_failing_state_probe_does_not_break_answer_submission(monkeypatch):
    """사용자는 폼을 이미 제출했다. 노트는 보조 지시이므로 제출을 막지 못한다."""
    _seed(monkeypatch, "fq9")
    async def boom():
        raise RuntimeError("s3 down")
    monkeypatch.setattr(registry.get("fq9"), "get_state", boom)
    r = _submit("fq9", {"1": "B"})
    assert r.status_code == 200, r.text
    assert r.json()["turn_id"]


# ---- 복원되는 말풍선이 사용자가 실제로 답한 것이어야 한다 ----
#
# **실측(2026-08-21).** 워크스페이스를 새로고침해 대화가 복원되면 사용자 말풍선이
# 전부 같은 기계 문구로 고정돼 있었다: "질문에 답했습니다. 답변은 `…-questions.md`의
# `[Answer]:` 태그에 들어 있으니, 파일을 읽고 멈춘 지점부터 이어가 주세요."
#
# 라이브 화면은 실제 답변을 그린다(useWorkspaceStream이 `answerSummary(questions,
# answers, t)`로 만든다). 즉 **같은 라운드가 새로고침 전후로 다르게 보였다** —
# `agent/answer_store.py` 헤더가 기록한 그 결함이 파일 질문 경로에서 되살아난
# 것이다. 그쪽은 `tool_use_id`로 조인해 고쳤는데, 파일 라운드에는 그 도구 호출이
# 아예 없어서(에이전트가 부르는 것은 `Write`다) 조인할 id도, 기록도 없었다.
#
# **불투명 마커로 조인하지 않는다.** `frontend/lib/approvalMarker.ts`가 이 부류에
# 대한 결정을 적어 뒀다 — "이 텍스트는 기계 신호가 아니다. 그 턴은 트랜스크립트와
# 채팅 히스토리에 사용자 말풍선으로 남는다. 에이전트가 이해해야 하고 사람이 읽어야
# 한다." 그래서 턴 텍스트 자체가 사람이 읽을 답변을 담는다: 트랜스크립트가 진실을
# 갖게 되므로 조인도, 새 상태도, 순서 의존도 생기지 않는다.

def test_the_resume_turn_carries_the_answers_the_user_actually_gave(monkeypatch):
    """말풍선이 될 텍스트에 답변이 있어야 한다 — 파일 포인터만으로는 복원이
    "질문에 답했습니다"에서 멈춘다."""
    _seed(monkeypatch, "fqans1")
    handle = _submit("fqans1", {"1": "B", "12": "A,C"}).json()["turn_id"]
    payload = app_module.turn_handles.consume("fqans1", handle)

    text = payload["text"]
    # 두 문항의 답변이 **읽을 수 있는 형태로** 온다. letter 리터럴("A,C")을 단정하지
    # 않는 이유: 2026-08-21에 렌더가 백엔드로 오면서 보기 라벨로 펼쳐진다 — 그
    # 펼침 자체는 위 두 테스트가 문항별로 고정한다.
    assert "플랫폼(Platform)" in text, text
    assert "신규 MD 온보딩 기간 단축률" in text, text
    # 파일 지목은 그대로 남는다 — 파일이 여전히 권위다(`[Answer]:` 태그가 정본).
    assert "strategy-questions.md" in text


def test_the_resume_turn_keeps_free_text_answers_verbatim(monkeypatch):
    """자유 답변은 사용자가 쓴 문장 그대로여야 한다 — 요약하거나 자르면 그것이
    복원된 대화의 기록이 된다."""
    _seed(monkeypatch, "fqans2")
    written = "예산은 3분기까지 확정되지 않았습니다. 그 전에는 B로 갑니다."
    handle = _submit("fqans2", {"1": written}).json()["turn_id"]
    payload = app_module.turn_handles.consume("fqans2", handle)

    assert written in payload["text"]


def test_the_resume_turn_follows_the_project_language(monkeypatch):
    """말풍선으로 남는 대화 텍스트다 — UI 언어가 아니라 프로젝트 언어다
    (`answer_first`·`approvalMarker.ts`가 같은 판단을 기록해 뒀다)."""
    _seed(monkeypatch, "fqans3", language="en")
    handle = _submit("fqans3", {"1": "B"}).json()["turn_id"]
    text = app_module.turn_handles.consume("fqans3", handle)["text"]

    # wrapper만 본다 — 본문은 질문 파일의 문장을 인용하고 이 픽스처는 한국어다
    # (fq8의 같은 주석에 근거가 있다).
    assert "I answered the questions" in text, text
    assert "질문에 답했습니다" not in text, text
    assert "Q1." in text


# ---- 기록되는 텍스트가 화면과 같아야 한다 (2026-08-21) ----
#
# 아침의 `fe6a482`는 답변을 턴 텍스트에 담았지만 **보기 letter 그대로**였다
# ("- 1: A,B"). 라이브 화면은 letter를 보기 라벨로 풀어 그리므로(당시
# frontend/lib/answerSummary.ts) 기록과 화면이 여전히 달랐다 — 결함의 절반만 고친
# 것이다. 이제 렌더가 백엔드 한 벌이고(aipds/answer_summary.py) 그 결과가 곧 턴
# 텍스트다.

def test_the_resume_turn_renders_option_labels_not_bare_letters(monkeypatch):
    """letter만으로는 읽는 사람에게 아무 뜻이 없다 — 복원된 대화가 "B"만 보여주면
    질문 폼을 다시 열어야 무슨 결정이었는지 알 수 있다."""
    _seed(monkeypatch, "lbl1")
    handle = _submit("lbl1", {"1": "B"}).json()["turn_id"]
    text = app_module.turn_handles.consume("lbl1", handle)["text"]

    assert "플랫폼(Platform)" in text, text
    # 문항 문장도 함께 온다 — 답변만 있으면 무엇에 대한 답인지 알 수 없다.
    assert "포지셔닝" in text, text
    assert "Q1." in text, text


def test_the_resume_turn_expands_every_letter_of_a_multi_select_answer(monkeypatch):
    """복수 선택은 콤마로 온다("A,C"). 그대로 기록하면 두 결정이 두 글자로 남는다."""
    _seed(monkeypatch, "lbl2")
    handle = _submit("lbl2", {"12": "A,C"}).json()["turn_id"]
    text = app_module.turn_handles.consume("lbl2", handle)["text"]

    assert "MD 업무 시간 절감률" in text, text
    assert "신규 MD 온보딩 기간 단축률" in text, text


def test_the_submit_response_carries_the_same_text_it_recorded(monkeypatch):
    """프론트가 말풍선을 다시 만들지 않게 하는 근거. 서버가 기록한 것과 프론트가
    보여주는 것이 **같은 문자열**이어야 갈라질 수 없다."""
    _seed(monkeypatch, "lbl3")
    body = _submit("lbl3", {"1": "B"}).json()
    recorded = app_module.turn_handles.consume("lbl3", body["turn_id"])["text"]

    assert body["summary"], "응답에 말풍선 텍스트가 없다"
    # 기록된 턴 텍스트는 모델용 지시가 뒤에 붙으므로 summary를 **포함**한다.
    assert body["summary"] in recorded, (body["summary"], recorded)
    assert "플랫폼(Platform)" in body["summary"]
