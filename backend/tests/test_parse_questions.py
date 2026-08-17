# backend/tests/test_parse_questions.py
from pathlib import Path
from pathfinder.parsers.questions import parse_question_file

FIX = Path(__file__).parent / "fixtures"

def _load(name): return parse_question_file(name, (FIX / name).read_text(encoding="utf-8"))

def test_flat_layout_single_question():
    qf = _load("discovery-mode-selection-questions.md")
    assert qf.parse_ok is True
    assert len(qf.questions) == 1
    q = qf.questions[0]
    assert q.number == 1
    assert q.category is None
    assert q.answer == "A"
    assert q.options[-1].is_other is True
    assert q.options[-1].letter == "C"

def test_categorized_layout_and_categories():
    qf = _load("strategy-questions.md")
    assert qf.parse_ok is True
    assert len(qf.questions) == 13
    assert qf.preamble is not None and "가정" in qf.preamble
    q1 = next(q for q in qf.questions if q.number == 1)
    assert q1.category == "Positioning"
    # A) option carries the recommendation marker in the source
    a_opt = next(o for o in q1.options if o.letter == "A")
    assert a_opt.recommended is True
    assert "←" not in a_opt.text

def test_multiselect_and_letter_answers_captured():
    qf = _load("strategy-questions.md")
    answers = {q.number: q.answer for q in qf.questions}
    assert answers[11] == "C"
    assert answers[12] == "A,B"

def test_unparseable_file_falls_back_to_raw():
    qf = parse_question_file("weird.md", "This has no questions at all.\nJust prose.")
    assert qf.parse_ok is False
    assert qf.questions == []
    assert qf.raw_markdown == "This has no questions at all.\nJust prose."

def test_empty_file_falls_back():
    qf = parse_question_file("empty.md", "")
    assert qf.parse_ok is False
    assert qf.raw_markdown == ""


# ---- 문항 헤더에 붙는 접미사 ----
# 2026-08-16 keumkang-v5: `design-context.md`의 문항 3개 중 하나가 파싱되지 않아
# 답변이 기록되지 않았다. 정규식이 숫자 뒤에 **아무것도 없어야** 한다고 요구했는데
# (`Question\s+(\d+)\s*$`), 에이전트는 후속 질문에 괄호 설명을 붙인다:
#
#   ## Question 4 (모호성 해소 — Question 3 답변에 따른 후속)
#
# 상류 형식(question-format-guide.md)은 `## Question [Number]`만 규정하고 접미사를
# 금지하지 않는다. 이 결함은 읽기 경로 전체에 번진다 — 화면의 질문 카드,
# answeredCount, 스테이지 진행률이 모두 이 파서를 통과한다.

def test_a_header_suffix_still_parses_as_that_question():
    md = """## Question 4 (모호성 해소 — Question 3 답변에 따른 후속)
모바일에서 어떤 작업을 하실 계획입니까?

A) 열람만
B) 전체 기능
X) Other (please describe after [Answer]: tag below)

[Answer]:
"""
    qf = parse_question_file("design-context.md", md)
    assert qf.parse_ok
    assert [q.number for q in qf.questions] == [4]
    # 헤더의 괄호 설명은 문항 **본문이 아니다** — 본문은 다음 줄부터다.
    assert qf.questions[0].text == "모바일에서 어떤 작업을 하실 계획입니까?"
    assert len(qf.questions[0].options) == 3


def test_numbering_may_skip_and_carry_suffixes_together():
    """실측 파일의 모양 그대로: 1 → 3 → 4(접미사). 번호는 건너뛸 수 있다."""
    md = "".join(f"""## Question {n}{suffix}
질문 {n}?

A) 하나
X) Other (please describe after [Answer]: tag below)

[Answer]:

""" for n, suffix in ((1, ""), (3, ""), (4, " (모호성 해소 — 후속)")))
    qf = parse_question_file("design-context.md", md)
    assert [q.number for q in qf.questions] == [1, 3, 4]


def test_a_heading_that_merely_starts_with_question_is_not_a_question():
    """`## Questions`나 번호 없는 `## Question`은 문항이 아니다 — 느슨하게 만든
    정규식이 카테고리 헤더를 삼키면 그 절 전체가 한 문항으로 뭉개진다."""
    for heading in ("## Questions 개요", "## Question 없음", "## Questionnaire"):
        qf = parse_question_file("x.md", heading + "\n\n본문\n")
        assert not qf.parse_ok, heading
