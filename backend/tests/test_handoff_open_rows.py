# backend/tests/test_handoff_open_rows.py — handoff/open_rows: candidates, the model's classification checked, the table and marks
from __future__ import annotations

import json

from aipds.handoff import open_rows, supplement
from aipds.handoff.package import _labels
from aipds.handoff.readiness import assess
from test_handoff_readiness import A1, D

LAB = _labels("ko")
SPEC = D + "prototype/prototype-spec.md"
HANDOFF = "decisions.md · 인계 시 보완(PM)"
LEDGER = ("| 근거 | 질문 | 답 | 누가 정했나 |\n|---|---|---|---|\n"
          "| go-to-market/gtm-questions.md Q12 | 출시 범위는? | 캡처·수동 입력 | AI 제안 그대로 |\n")

_SPEC_TEXT = """# 명세

## 기능
- 보드

## 미해결 질문

1. **정확도 산정 방식**: 보고된 값의 집계 규칙이
   기록에 없습니다.
2. **gen 2·4 표기**: 빌드 기록에 gen 2·4 제목이 없습니다.

## 다음 절
- 무관
"""


def _collect(record=None, material=None, previous=None):
    material = {SPEC: _SPEC_TEXT} if material is None else material
    return open_rows.collect(assess(A1, {}), record or supplement.Supplement(), material, previous, LAB,
                             {}, HANDOFF)


# ---- candidates ----

def test_a_specs_own_open_questions_become_candidates_item_by_item():
    assert open_rows.spec_items(_SPEC_TEXT) == [
        ("미해결 질문", "**정확도 산정 방식**: 보고된 값의 집계 규칙이 기록에 없습니다."),
        ("미해결 질문", "**gen 2·4 표기**: 빌드 기록에 gen 2·4 제목이 없습니다.")]
    candidates, closed = _collect()
    assert [(c.kind, c.source) for c in candidates] == [("spec", "prototype/prototype-spec.md · 미해결 질문")] * 2
    assert candidates[0].text == "정확도 산정 방식: 보고된 값의 집계 규칙이 기록에 없습니다."
    assert closed == []
    # The key is the item, not its position: the same item keeps its key across versions.
    again, _ = _collect(material={SPEC: _SPEC_TEXT.replace("1. **정확도", "0. **정확도")})
    assert again[0].key == candidates[0].key


def test_a_handoff_answer_merges_into_its_row_or_closes_it():
    """The PM's "being checked" stays attached to the row it came from; an answer closes the row before the model sees it."""
    rows = [open_rows.Row(id="O-01", key="quote:a", question="보존 기간은?", who="PM", when="빌드 전", until="—",
                          source="discovery-document.md", quote="보존 기간은 정해야 한다", file="discovery-document.md"),
            open_rows.Row(id="O-02", key="quote:b", question="링크 유효 기간은?", who="PM", when="빌드 전", until="—",
                          source="discovery-document.md", quote="링크 유효 기간은 미정이다", file="discovery-document.md")]
    material = {D + "discovery-document.md": "보존 기간은 정해야 한다. 링크 유효 기간은 미정이다."}
    record = supplement.Supplement(open_answers={
        supplement.open_key("보존 기간은?"): supplement.OpenAnswer(question="보존 기간은?", pending_with="법무팀"),
        supplement.open_key("링크 유효 기간은?"): supplement.OpenAnswer(question="링크 유효 기간은?", text="7일"),
        supplement.open_key("새 질문"): supplement.OpenAnswer(question="새 질문", unknown=True)})
    candidates, closed = _collect(record, material, open_rows.Record(rows=rows))
    assert [(c.key, c.question, c.until) for c in candidates] == [
        ("quote:a", "보존 기간은?", "확인 중: 법무팀"),
        ("handoff:" + supplement.open_key("새 질문"), "새 질문", "")]
    assert [(c.key, c.reason) for c in closed] == [("quote:b", "answered")]


def test_a_quote_backed_row_comes_back_only_while_its_quote_is_there():
    row = open_rows.Row(id="O-01", key="quote:a", question="보존 기간은?", who="PM", when="빌드 전", until="—",
                        source="discovery-document.md · 조건", quote="보존 기간은 정해야 한다", file="discovery-document.md")
    kept, _ = _collect(material={D + "discovery-document.md": "조건: 보존 기간은\n정해야 한다."},
                       previous=open_rows.Record(rows=[row]))
    assert [(c.kind, c.question) for c in kept] == [("quote", "보존 기간은?")]
    gone, closed = _collect(material={D + "discovery-document.md": "보존 기간은 3년이다."},
                            previous=open_rows.Record(rows=[row]))
    assert gone == [] and [(c.key, c.reason) for c in closed] == [("quote:a", "source_changed")]


def test_missing_topics_and_a_lagging_spec_are_candidates():
    paths = [D + "prototypes/a/PROTOTYPE-a.md", D + "prototypes/a/change-history.md"]
    state = assess(paths, {D + "prototypes/a/change-history.md": "## 2026-10-13 16:27 UTC · gen 1\n\n검색 제거\n"})
    record = supplement.Supplement(answers={"problem.evidence": supplement.Answer(text="인터뷰"),
                                            "problem.why_now": supplement.Answer(unknown=True)})
    candidates, _ = open_rows.collect(state, record, {}, None, LAB, {"problem.why_now": "지금 풀어야 하는 이유"}, HANDOFF)
    kinds = [(c.kind, c.source) for c in candidates]
    assert kinds[0] == ("reconcile", "prototypes/a/change-history.md")
    assert ("topic", HANDOFF) in kinds and ("topic", "재료 없음") in kinds
    assert not any(c.key == "topic:problem.evidence" for c in candidates)


# ---- the model's classification, checked ----

def _candidates():
    spec, other = _collect()[0]
    declared = open_rows.Candidate(key="handoff:x", kind="handoff", text="새 질문", question="새 질문",
                                   source=HANDOFF, until="")
    return [spec, other, declared]


MATERIAL = {D + "discovery-document.md": "출시 범위: 캡처·수동 입력. 로그인 방식은 아직 정하지 않았다.", SPEC: _SPEC_TEXT,
            "decisions.md": LEDGER}


def test_the_model_places_candidates_and_quoted_additions():
    reply = {"questions": [
        {"id": "O-01", "from": "C1", "question": "정확도는 어떻게 셉니까?", "who": "PM", "when": "**빌드 전**",
         "until": "x", "source": "prototype/prototype-spec.md · 미해결 질문"},
        {"id": "O-02", "question": "로그인 방식은?", "who": "PM", "when": "빌드 중", "until": "사내 계정으로 시작",
         "source": "discovery-document.md · 시작하기",
         "quote": {"file": "discovery-document.md", "text": "로그인 방식은 아직 정하지 않았다"}},
        {"id": "O-03", "question": "지어낸 질문", "who": "PM", "when": "빌드 전",
         "quote": {"file": "discovery-document.md", "text": "원본에 없는 문장을 인용한다"}}],
        "closed": [{"from": "C2", "reason": "not_product"}]}
    table = open_rows.resolve(reply, _candidates(), [], MATERIAL, LEDGER, LAB)
    assert [(r.id, r.question, r.who, r.when, r.until) for r in table.rows] == [
        ("O-01", "정확도는 어떻게 셉니까?", "PM", "빌드 전", "—"),
        ("O-02", "로그인 방식은?", "PM", "빌드 중", "사내 계정으로 시작"),
        # Not placed: a conservative row, in the PM's own words.
        ("O-03", "새 질문", "PM", "빌드 전", "—")]
    assert table.rows[1].quote == "로그인 방식은 아직 정하지 않았다" and table.rows[1].key.startswith("quote:")
    assert table.rejected == ["지어낸 질문"]
    assert [(c.reason, c.question) for c in table.closed] == [
        ("not_product", "gen 2·4 표기: 빌드 기록에 gen 2·4 제목이 없습니다.")]


def test_a_closing_reason_that_does_not_hold_reopens_the_candidate():
    reply = {"closed": [
        {"from": "C1", "reason": "decided", "by": "decisions.md · go-to-market/gtm-questions.md Q1"},  # Q12 is not Q1
        {"from": "C2", "reason": "decided", "by": "decisions.md · go-to-market/gtm-questions.md Q12"},
        {"from": "C3", "reason": "out_of_scope"}]}       # the PM declared it open: only the PM closes it
    table = open_rows.resolve(reply, _candidates(), [], MATERIAL, LEDGER, LAB)
    assert [r.question for r in table.rows] == [
        "정확도 산정 방식: 보고된 값의 집계 규칙이 기록에 없습니다.", "새 질문"]
    assert [(c.reason, c.by) for c in table.closed] == [
        ("decided", "decisions.md · go-to-market/gtm-questions.md Q12")]


def test_a_duplicate_closes_only_into_a_row_that_is_kept():
    reply = {"questions": [{"id": "O-01", "from": "C1", "question": "정확도?", "who": "PM", "when": "빌드 전"}],
             "closed": [{"from": "C2", "reason": "duplicate", "by": "C1"},
                        {"from": "C3", "reason": "duplicate", "by": "C9"}]}
    table = open_rows.resolve(reply, _candidates(), [], MATERIAL, LEDGER, LAB)
    assert [r.question for r in table.rows] == ["정확도?", "새 질문"]
    assert [c.reason for c in table.closed] == ["duplicate"]


def test_classification_falls_to_the_conservative_side():
    reply = {"questions": [
        {"id": "O-01", "from": "C1", "question": "a", "who": "법무팀", "when": "나중에"},
        {"id": "O-01", "from": "C2", "question": "b", "who": "개발팀", "when": "빌드 중", "until": ""}]}
    rows = open_rows.resolve(reply, _candidates()[:2], [], MATERIAL, LEDGER, LAB).rows
    # Unknown who → PM, unknown when → before build, during build with no way to build meanwhile → before build;
    # a repeated ID is renumbered.
    assert [(r.id, r.who, r.when, r.until) for r in rows] == [
        ("O-02", "PM", "빌드 전", "—"), ("O-01", "개발팀", "빌드 전", "—")]


def test_a_reply_without_json_keeps_every_candidate():
    reply, body = open_rows.split_reply("설명.\n# 제목 — Vision\n본문\n")
    assert reply == {} and body.startswith("# 제목")
    reply, body = open_rows.split_reply('```json\n{"closed": []}\n```\n# 제목 — Vision\n')
    assert reply == {"closed": []} and body == "# 제목 — Vision\n"
    assert open_rows.split_reply("```json\n{broken\n```\n# 제목\n")[0] == {}
    assert len(open_rows.resolve({}, _candidates(), [], MATERIAL, LEDGER, LAB).rows) == 3


# ---- the table and the marks ----

def test_the_table_replaces_whatever_the_model_wrote_under_open_questions():
    doc = ("# P — Vision\n\n## Risks and Dependencies\n### Open Questions\n\n| 모델이 쓴 표 |\n|---|\n\n"
           "## Appendix\n본문\n")
    table = open_rows.render(open_rows.Record(rows=[open_rows.Row(
        id="O-01", key="k", question="a|b", who="PM", when="빌드 전", until="—", source="x.md")]), LAB)
    out = open_rows.insert(doc, table)
    assert "모델이 쓴 표" not in out and "| O-01 | a\\|b | PM | 빌드 전 | — | x.md |" in out
    assert out.index("| O-01") < out.index("## Appendix") and out.endswith("본문\n")
    # No section: the heading and table go at the end.
    assert open_rows.insert("# P\n", table).endswith("### Open Questions\n\n" + table + "\n")


def test_marks_follow_ledger_citations_not_the_model():
    kept = {("go-to-market/gtm-questions.md", 13)}
    doc = ("# P\n"
           "- 목표 5분 이하 [decisions.md · go-to-market/gtm-questions.md Q13]\n"
           "- 모델이 붙인 표시 (AI 제안, PM 미확인) [discovery-document.md · 요약]\n"
           "| 사용률 | 30일 | 집계 [decisions.md · go-to-market/gtm-questions.md Q12, Q13] |\n"
           "- 확인된 결정 [decisions.md · go-to-market/gtm-questions.md Q14]\n"
           "### Open Questions\n"
           "- 표 안은 그대로 [decisions.md · go-to-market/gtm-questions.md Q13]\n")
    out = open_rows.mark(doc, kept, LAB["ai_kept"], "decisions.md").splitlines()
    assert out[1] == "- 목표 5분 이하 (AI 제안, PM 미확인) [decisions.md · go-to-market/gtm-questions.md Q13]"
    assert out[2] == "- 모델이 붙인 표시 [discovery-document.md · 요약]"
    assert out[3] == "| 사용률 | 30일 | 집계 (AI 제안, PM 미확인) [decisions.md · go-to-market/gtm-questions.md Q12, Q13] |"
    assert out[4] == "- 확인된 결정 [decisions.md · go-to-market/gtm-questions.md Q14]"
    assert out[6] == "- 표 안은 그대로 [decisions.md · go-to-market/gtm-questions.md Q13]"


def test_the_record_round_trips():
    record = open_rows.Record(rows=[open_rows.Row(id="O-01", key="k", question="q", who="PM", when="빌드 전",
                                                  until="—", source="s")],
                              closed=[open_rows.Closed(key="c", question="q2", reason="answered")])
    assert open_rows.Record.model_validate_json(json.dumps(json.loads(record.model_dump_json()))) == record
