# backend/tests/test_handoff_supplement.py — handoff/supplement and its routes
#
# Assessment inputs use the observed shapes from test_handoff_readiness (A1: ws-g, B: ws-n).
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
    """The agent neither reads nor writes supplementary answers; placing them under a prefix that
    is restored into the workspace or published to the source of truth would break that condition."""
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
    # AI-PLC artifacts are not touched.
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


# ---- The PM's share of open decisions ----

def _open(*questions):
    return [supplement.OpenQuestion(id=f"O-{i:02d}", question=q) for i, q in enumerate(questions, 1)]


def test_open_pm_questions_of_the_prd_are_asked_even_when_every_section_is_sourced():
    """Observed (ws-g): all 9 sections were sourced, so there were 0 supplementary questions, yet
    PRD item 9 had an open PM decision blocking implementation."""
    v = supplement.view(assess(A1, {}), supplement.Supplement(),
                        _open("보존 기간", "본인 확인 수준", "보존 기간"))
    assert v.questions == []
    assert [(q.id, q.question) for q in v.open_questions] == [("O-01", "보존 기간"),
                                                             ("O-02", "본인 확인 수준")]
    assert all(q.key == supplement.open_key(q.question) for q in v.open_questions)


def test_an_answered_question_that_left_the_prd_stays_on_record():
    answer = supplement.OpenAnswer(question="보존 기간", text="5년", updated_at="t")
    record = supplement.Supplement(open_answers={supplement.open_key("보존 기간"): answer})
    v = supplement.view(assess(A1, {}), record, _open("본인 확인 수준"))
    assert [q.question for q in v.open_questions] == ["본인 확인 수준"]
    assert [(q.question, q.answer.text) for q in v.open_answered] == [("보존 기간", "5년")]


def test_apply_keeps_open_answers_and_their_time():
    key = supplement.open_key("보존 기간")
    first = supplement.apply(supplement.Supplement(), supplement.SupplementUpdate(open_answers={
        key: supplement.OpenAnswerIn(question="보존  기간", text=" 5년 ")}), now="t1")
    assert first.open_answers[key].model_dump() == {
        "text": "5년", "unknown": False, "updated_at": "t1", "question": "보존 기간",
        "pending_with": ""}
    again = supplement.apply(first, supplement.SupplementUpdate(open_answers={
        key: supplement.OpenAnswerIn(question="보존 기간", text="5년")}), now="t2")
    assert again.open_answers[key].updated_at == "t1"


def test_apply_rejects_an_open_answer_whose_key_is_not_its_question():
    """If the key is not derived from the question text, it can overwrite another question's answer."""
    with pytest.raises(supplement.InvalidSupplement):
        supplement.apply(supplement.Supplement(), supplement.SupplementUpdate(open_answers={
            supplement.open_key("보존 기간"): supplement.OpenAnswerIn(question="권한", text="x")}))


def test_only_decisions_needed_before_build_are_asked():
    """Observed (ws-g): of the PM's 18 decisions, 7 blocked implementation. Decisions for during the
    build are collapsed, and options are attached only to decisions needed before the build."""
    questions = [
        supplement.OpenQuestion(id="O-01", question="보존 기간 (R-08)"),
        supplement.OpenQuestion(id="O-02", question="지원 외국어 목록 (R-02)", blocking=False,
                                until="목록을 바꿀 수 있게 만들고 영어·베트남어로 시작"),
    ]
    options = {supplement.open_key(q.question): [supplement.Option(text="3년", source="a.md"),
                                                 supplement.Option(text="5년", source="a.md")]
               for q in questions}
    answer = supplement.OpenAnswer(question="지원 외국어 목록 (R-02)", text="영어만")
    record = supplement.Supplement(open_answers={supplement.open_key(answer.question): answer})
    v = supplement.view(assess(A1, {}), record, questions, options)
    assert [(q.id, [o.text for o in q.options]) for q in v.open_questions] == [
        ("O-01", ["3년", "5년"])]
    assert [(q.id, q.until, q.options) for q in v.open_deferred] == [
        ("O-02", "목록을 바꿀 수 있게 만들고 영어·베트남어로 시작", [])]
    # An answer to a during-build decision stays next to that question; it does not move into "previously answered questions".
    assert v.open_deferred[0].answer.text == "영어만" and v.open_answered == []


def test_the_key_of_an_open_question_ignores_the_ids_it_points_at():
    """Requirement numbers change on every regeneration. An answer must not lose its question just because R-08 became R-09."""
    assert (supplement.open_key("보존 기간은? (R-08, C-05)") == supplement.open_key("보존 기간은? (R-09)")
            == supplement.open_key("보존  기간은?"))
    assert supplement.open_key("보존 기간은?") != supplement.open_key("링크 유효 기간은?")


def test_an_external_check_has_no_options_and_can_be_answered_with_who_is_checking():
    """Observed (ws-g): "증빙 효력을 갖는가?" ("does it have evidentiary force?") got "인정한다"
    ("acknowledged") as an option, which has the PM picking a fact that legal should answer."""
    q = supplement.OpenQuestion(id="O-01", question="전자 기록의 증빙 효력은? (C-01)", external=True)
    key = supplement.open_key(q.question)
    options = {key: [supplement.Option(text="인정", source="a.md"),
                     supplement.Option(text="불인정", source="a.md")]}
    v = supplement.view(assess(A1, {}), supplement.Supplement(), [q], options)
    assert v.open_questions[0].external and v.open_questions[0].options == []

    record = supplement.apply(supplement.Supplement(), supplement.SupplementUpdate(open_answers={
        key: supplement.OpenAnswerIn(question=q.question, pending_with=" 법무팀 ")}), now="t1")
    assert record.open_answers[key].pending_with == "법무팀" and record.open_answers[key].text == ""
    again = supplement.apply(record, supplement.SupplementUpdate(open_answers={
        key: supplement.OpenAnswerIn(question=q.question, pending_with="법무팀(10/20 회신)")}), now="t2")
    assert again.open_answers[key].updated_at == "t2"
