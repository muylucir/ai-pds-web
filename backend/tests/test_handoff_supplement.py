# backend/tests/test_handoff_supplement.py — handoff/supplement와 그 라우트
#
# 판정 입력은 test_handoff_readiness의 실측 모양(A1: industry-safe-law, B: test2222)을 쓴다.
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import aipds.app as app_module
from aipds.app import app, registry
from aipds.handoff import supplement
from aipds.handoff.readiness import assess
from aipds.runner import AgentRunner
from aipds.workspace import Workspace
from aipds.workspace_sync import is_synced_key
from fakes.fake_runner import FakeRunner
from fakes.in_memory_s3 import FakeS3Store
from test_handoff_readiness import A1, B, D, QUESTIONS

STRATEGY = D + "product-strategy/strategy-questions.md"


def _ids(view):
    return [q.id for q in view.questions]


def test_path_b_asks_every_pm_only_question():
    v = supplement.view(assess(B, {}), supplement.Supplement())
    assert _ids(v) == [qid for qid, _ in supplement.QUESTIONS]
    assert all(q.answer is None for q in v.questions)


def test_a_fully_sourced_project_asks_nothing():
    v = supplement.view(assess(A1, {}), supplement.Supplement())
    assert v.questions == [] and v.superseded == []


def test_an_answer_whose_section_got_sourced_later_is_superseded_not_dropped():
    record = supplement.Supplement(answers={
        "problem.evidence": supplement.Answer(text="인터뷰 5명", updated_at="t")})
    v = supplement.view(assess(A1, {}), record)
    assert [q.id for q in v.superseded] == ["problem.evidence"]
    assert v.superseded[0].answer.text == "인터뷰 5명"


def test_accepted_ai_suggestions_are_listed_for_confirmation():
    state = assess(A1, {STRATEGY: QUESTIONS})
    record = supplement.Supplement(confirmed={STRATEGY + "#2": "t0"})
    v = supplement.view(state, record)
    assert [c.key for c in v.confirmations] == [STRATEGY + "#1", STRATEGY + "#2", STRATEGY + "#4"]
    assert [c.confirmed_at for c in v.confirmations] == [None, "t0", None]


def test_apply_keeps_the_time_of_unchanged_answers():
    first = supplement.apply(supplement.Supplement(), supplement.SupplementUpdate(
        answers={"problem.evidence": supplement.AnswerIn(text="  인터뷰 5명 ")}), now="t1")
    assert first.answers["problem.evidence"].text == "인터뷰 5명"
    second = supplement.apply(first, supplement.SupplementUpdate(answers={
        "problem.evidence": supplement.AnswerIn(text="인터뷰 5명"),
        "problem.why_now": supplement.AnswerIn(unknown=True)}), now="t2")
    assert second.answers["problem.evidence"].updated_at == "t1"
    assert second.answers["problem.why_now"].unknown is True
    assert second.answers["problem.why_now"].updated_at == "t2"


def test_apply_drops_blank_answers_and_replaces_the_form():
    previous = supplement.Supplement(answers={
        "goals.success": supplement.Answer(text="x", updated_at="t0")})
    record = supplement.apply(previous, supplement.SupplementUpdate(answers={
        "problem.evidence": supplement.AnswerIn(text="   ")}), now="t1")
    assert record.answers == {}


def test_apply_keeps_the_first_confirmation_time():
    key = STRATEGY + "#1"
    first = supplement.apply(supplement.Supplement(),
                             supplement.SupplementUpdate(confirmed=[key]), now="t1")
    again = supplement.apply(first, supplement.SupplementUpdate(confirmed=[key, key]), now="t2")
    assert again.confirmed == {key: "t1"}


@pytest.mark.parametrize("update", [
    supplement.SupplementUpdate(answers={"requirements.extra": supplement.AnswerIn(text="x")}),
    supplement.SupplementUpdate(confirmed=["../etc/passwd#1"]),
    supplement.SupplementUpdate(confirmed=[D + "discovery-document.md#1"]),
])
def test_apply_rejects_unknown_ids_and_malformed_keys(update):
    with pytest.raises(supplement.InvalidSupplement):
        supplement.apply(supplement.Supplement(), update)


def test_the_record_lives_outside_what_the_agent_restores_or_publishes():
    """에이전트는 보완 답을 읽지도 쓰지도 않는다 — 워크스페이스로 복원되거나 정본으로 올라가는
    접두사에 들어가면 그 조건이 깨진다."""
    assert not is_synced_key(supplement.SUPPLEMENT_KEY)
    assert not supplement.SUPPLEMENT_KEY.startswith(AgentRunner._RESTORE_PREFIXES)


def test_load_treats_a_corrupt_record_as_empty():
    s3 = FakeS3Store()
    s3.blobs[supplement.SUPPLEMENT_KEY] = "{not json"
    assert asyncio.run(supplement.load(s3)) == supplement.Supplement()


client = TestClient(app)


def _project(monkeypatch, pid, files):
    monkeypatch.setenv("AIPDS_S3_BUCKET", "")
    s3 = FakeS3Store()
    monkeypatch.setattr(app_module, "s3_store_factory", lambda project_id: s3)
    async def make(project_id):
        return Workspace(FakeRunner())
    monkeypatch.setattr(app_module, "make_workspace", make)
    assert client.post("/projects", json={"project_id": pid}).status_code == 200
    ws = registry.get(pid)
    async def seed():
        for path, content in files.items():
            await ws.runner.write_file(path, content)
    asyncio.get_event_loop().run_until_complete(seed())
    return s3


def test_route_round_trip(monkeypatch):
    files = {p: "# x\n" for p in B} | {D + "use-case-intake/use-case-intake-questions.md": QUESTIONS}
    s3 = _project(monkeypatch, "supp-b", files)

    r = client.get("/projects/supp-b/handoff/supplement")
    assert r.status_code == 200
    body = r.json()
    assert len(body["questions"]) == len(supplement.QUESTIONS)
    first_key = body["confirmations"][0]["key"]

    r = client.put("/projects/supp-b/handoff/supplement", json={
        "answers": {"problem.evidence": {"text": "인터뷰 5명"},
                    "assumptions.failure_reasons": {"unknown": True}},
        "confirmed": [first_key]})
    assert r.status_code == 200
    by_id = {q["id"]: q["answer"] for q in r.json()["questions"]}
    assert by_id["problem.evidence"]["text"] == "인터뷰 5명"
    assert by_id["assumptions.failure_reasons"]["unknown"] is True
    assert r.json()["confirmations"][0]["confirmed_at"]

    stored = json.loads(s3.blobs[supplement.SUPPLEMENT_KEY])
    assert set(stored["answers"]) == {"problem.evidence", "assumptions.failure_reasons"}
    # AI-PLC 산출물은 건드리지 않는다.
    assert not any(k.startswith("aiplc-docs/") for k in s3.blobs)


def test_route_rejects_unknown_question_ids(monkeypatch):
    _project(monkeypatch, "supp-bad", {p: "# x\n" for p in B})
    r = client.put("/projects/supp-bad/handoff/supplement",
                   json={"answers": {"requirements.extra": {"text": "x"}}})
    assert r.status_code == 422


def test_route_rejects_an_oversized_answer(monkeypatch):
    _project(monkeypatch, "supp-long", {p: "# x\n" for p in B})
    r = client.put("/projects/supp-long/handoff/supplement", json={
        "answers": {"problem.evidence": {"text": "x" * (supplement.MAX_ANSWER_CHARS + 1)}}})
    assert r.status_code == 422


# ---- PRD에 남은 PM 결정 ----

def _open(*questions):
    return [supplement.OpenQuestion(id=f"O-{i:02d}", question=q) for i, q in enumerate(questions, 1)]


def test_open_pm_questions_of_the_prd_are_asked_even_when_every_section_is_sourced():
    """실측(industry-safe-law): 9개 섹션이 모두 sourced라 보완 질문이 0개였는데, PRD 9번에는 구현을
    막는 PM 결정이 열려 있었다."""
    v = supplement.view(assess(A1, {}), supplement.Supplement(), {},
                        _open("보존 기간", "본인 확인 수준", "보존 기간"))
    assert v.questions == []
    assert [(q.id, q.question) for q in v.open_questions] == [("O-01", "보존 기간"),
                                                             ("O-02", "본인 확인 수준")]
    assert all(q.key == supplement.open_key(q.question) for q in v.open_questions)


def test_an_answered_question_that_left_the_prd_stays_on_record():
    answer = supplement.OpenAnswer(question="보존 기간", text="5년", updated_at="t")
    record = supplement.Supplement(open_answers={supplement.open_key("보존 기간"): answer})
    v = supplement.view(assess(A1, {}), record, {}, _open("본인 확인 수준"))
    assert [q.question for q in v.open_questions] == ["본인 확인 수준"]
    assert [(q.question, q.answer.text) for q in v.open_answered] == [("보존 기간", "5년")]


def test_apply_keeps_open_answers_and_their_time():
    key = supplement.open_key("보존 기간")
    first = supplement.apply(supplement.Supplement(), supplement.SupplementUpdate(open_answers={
        key: supplement.OpenAnswerIn(question="보존  기간", text=" 5년 ")}), now="t1")
    assert first.open_answers[key].model_dump() == {
        "text": "5년", "unknown": False, "updated_at": "t1", "question": "보존 기간"}
    again = supplement.apply(first, supplement.SupplementUpdate(open_answers={
        key: supplement.OpenAnswerIn(question="보존 기간", text="5년")}), now="t2")
    assert again.open_answers[key].updated_at == "t1"


def test_apply_rejects_an_open_answer_whose_key_is_not_its_question():
    """키가 질문 문장에서 나오지 않으면 다른 질문의 답을 덮어쓸 수 있다."""
    with pytest.raises(supplement.InvalidSupplement):
        supplement.apply(supplement.Supplement(), supplement.SupplementUpdate(open_answers={
            supplement.open_key("보존 기간"): supplement.OpenAnswerIn(question="권한", text="x")}))
