# backend/tests/test_handoff_ledger.py — handoff/ledger: decision ledger
from __future__ import annotations

import re

from aipds.handoff import ledger, supplement
from test_handoff_readiness import D, QUESTIONS

_PATH = D + "product-strategy/strategy-questions.md"


def _rows(text: str) -> list[list[str]]:
    """Table line → cells. An escaped pipe (`\\|`) is not a cell boundary."""
    return [[c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
            for line in text.splitlines() if line.startswith("| ") and "---" not in line]


def test_the_ledger_says_who_decided_each_answer():
    """89% of questions where the AI displayed a default were picked as-is (observed) — those are the decisions development should recheck."""
    record = supplement.Supplement(confirmed={_PATH + "#2": "t"})
    text = ledger.build({_PATH: QUESTIONS, D + "discovery-document.md": "# 문서\n"}, record, "ko")
    assert text.startswith("# 결정 장부")
    assert "## 제품 전략" in text
    rows = _rows(text)
    assert rows[0] == ["근거", "질문", "답", "누가 정했나"]
    by_q = {r[0]: r for r in rows[1:]}
    assert by_q["product-strategy/strategy-questions.md Q1"][2:] == ["Subscription", "AI 제안 그대로"]
    # Elaborations are appended to the answer, and what the PM confirmed right before handoff is shown separately.
    assert by_q["product-strategy/strategy-questions.md Q2"][2:] == [
        "Premium — 단, 첫 해는 할인", "AI 제안 · PM 확인"]
    assert by_q["product-strategy/strategy-questions.md Q3"][2:] == ["Partners", "PM 선택"]
    # The AI's suggestion note (after ←) is not an answer.
    assert "제안: 페인 포인트" not in text


def test_handoff_answers_are_listed_as_they_were_given():
    q = "증빙 효력은?"
    record = supplement.Supplement(
        answers={"problem.evidence": supplement.Answer(text="현장 인터뷰 | 12명", updated_at="t1")},
        open_answers={
            supplement.open_key(q): supplement.OpenAnswer(question=q, pending_with="법무팀",
                                                          updated_at="t2"),
            supplement.open_key("링크 유효기간"): supplement.OpenAnswer(
                question="링크 유효기간", unknown=True, updated_at="t3")})
    text = ledger.build({}, record, "ko")
    rows = _rows(text.split("## 인계 시 보완(PM)", 1)[1])
    assert rows[1:] == [["문제가 실제로 있다는 근거", "현장 인터뷰 \\| 12명", "t1"],
                        ["증빙 효력은?", "확인 중: 법무팀", "t2"],
                        ["링크 유효기간", "모름 (열린 결정으로 넘김)", "t3"]]


def test_the_ledger_is_english_for_english_projects():
    text = ledger.build({_PATH: QUESTIONS}, supplement.Supplement(), "en")
    assert text.startswith("# Decision ledger") and "AI suggestion kept" in text
