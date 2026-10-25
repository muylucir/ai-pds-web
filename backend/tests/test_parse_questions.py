# backend/tests/test_parse_questions.py
from pathlib import Path
from aipds.parsers.questions import parse_question_file, serialize_answers

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


# ---- Suffixes attached to question headers ----
# 2026-08-16 ws-a-v5: one of the 3 questions in `design-context.md` failed to parse, so its
# answer was not recorded. The regex required **nothing** after the number
# (`Question\s+(\d+)\s*$`), but the agent attaches parenthetical notes to follow-up questions:
#
#   ## Question 4 (모호성 해소 — Question 3 답변에 따른 후속)
#
# The upstream format (question-format-guide.md) prescribes only `## Question [Number]` and does not
# forbid suffixes. This defect spreads across the whole read path — the on-screen question card,
# answeredCount, and stage progress all go through this parser.

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
    # The parenthetical note in the header is **not the question body** — the body starts on the next line.
    assert qf.questions[0].text == "모바일에서 어떤 작업을 하실 계획입니까?"
    assert len(qf.questions[0].options) == 3


def test_numbering_may_skip_and_carry_suffixes_together():
    """Exactly the shape of the observed file: 1 → 3 → 4 (suffix). Numbers can skip."""
    md = "".join(f"""## Question {n}{suffix}
질문 {n}?

A) 하나
X) Other (please describe after [Answer]: tag below)

[Answer]:

""" for n, suffix in ((1, ""), (3, ""), (4, " (모호성 해소 — 후속)")))
    qf = parse_question_file("design-context.md", md)
    assert [q.number for q in qf.questions] == [1, 3, 4]


def test_a_heading_that_merely_starts_with_question_is_not_a_question():
    """`## Questions` or an unnumbered `## Question` is not a question — if a loosened
    regex swallowed category headers, that whole section would be mashed into one question."""
    for heading in ("## Questions 개요", "## Question 없음", "## Questionnaire"):
        qf = parse_question_file("x.md", heading + "\n\n본문\n")
        assert not qf.parse_ok, heading


# ---- Modifiers before the question header, and four hashes ----
# 2026-08-17 test-wf: of 8 question files, `pain-point-clarification-questions.md` was read with
# **0 questions** and fell back to raw markdown, and that file's answers were not recorded
# (1 `[Answer]` slot, 0 filled). The regex required `Question` immediately after the hashes,
# but that file writes:
#
#   ### Clarification Question 1
#
# **This is a format upstream prescribes.** `question-format-guide.md` carries both
# `## Question [Number]` (line 22) and `### Clarification Question 1` (line 223, "Creating Clarification
# Questions") as templates. Across the rule set there is also a four-hash form
# (`#### Question 1: Brand & Design Context`).
#
# The number is still the only discriminator: even with modifiers allowed, `### Question File Format` and
# `### Context Questions (Per Use Case)` have no number and are not caught. Only a **single-word**
# modifier is allowed — swallowing reference prose headings like `## Answer to Question 3` would mash
# that section into one question (the same failure the test above blocks).

def test_a_qualified_question_heading_parses():
    """`### Clarification Question 1` — the format from upstream question-format-guide.md:223."""
    md = """### Clarification Question 1
프로토타입이 가장 먼저 증명해야 하는 것은 어느 쪽입니까?

A) 근거 조회 화면
B) 반복 유형 리포트
X) 기타 (아래 [Answer]: 태그 뒤에 설명해 주세요)

[Answer]:
"""
    qf = parse_question_file("pain-point-clarification-questions.md", md)
    assert qf.parse_ok
    assert [q.number for q in qf.questions] == [1]
    assert qf.questions[0].ask == "프로토타입이 가장 먼저 증명해야 하는 것은 어느 쪽입니까?"
    assert [o.letter for o in qf.questions[0].options] == ["A", "B", "X"]


def test_a_four_hash_question_heading_parses():
    """`#### Question 1: Brand & Design Context` — a form that actually exists in the rule set."""
    md = """#### Question 1: Brand & Design Context
어떤 브랜드 톤을 원하십니까?

A) 차분한
B) 대담한

[Answer]:
"""
    qf = parse_question_file("design-context.md", md)
    assert qf.parse_ok
    assert [q.number for q in qf.questions] == [1]
    # The title after the colon is a header suffix, not the body (same contract as the suffix tests).
    assert qf.questions[0].ask == "어떤 브랜드 톤을 원하십니까?"


def test_a_heading_that_names_question_without_a_number_is_still_not_a_question():
    """Even with modifiers allowed, without a number it is not a question.

    All of these are headings that really exist in the rule set — if they were caught as questions,
    that whole section would be mashed into one question."""
    for heading in ("### Question File Format",
                    "### Context Questions (Per Use Case)",
                    "## MANDATORY: Question File Format",
                    "### ⛔ GATE: Await PRFAQ Clarifying Question Answers",
                    # Only one-word modifiers — reference prose headings are not swallowed.
                    "## Answer to Question 3"):
        qf = parse_question_file("x.md", heading + "\n\n본문\n")
        assert not qf.parse_ok, heading


def test_answers_are_written_back_to_a_qualified_heading():
    """Parsing alone is not enough — the write-back must also run for this defect to be closed.

    What was lost on 2026-08-17 test-wf was not the parse result but the **answers**.
    `serialize_answers` finds slots by number, so header recognition is the write-back."""
    md = """### Clarification Question 1
어느 쪽입니까?

A) 왼쪽
B) 오른쪽

[Answer]:

### Clarification Question 2
언제입니까?

A) 지금
B) 나중

[Answer]:
"""
    out = serialize_answers(md, {1: "B", 2: "A"})
    assert "[Answer]: B" in out
    assert "[Answer]: A" in out
    # Re-reading must catch the same numbers (invariant: parser and serializer use the same criterion).
    qf = parse_question_file("c.md", out)
    assert [(q.number, q.answer) for q in qf.questions] == [(1, "B"), (2, "A")]


# ---- Question.context — prose between the category header and the question header ----
# 2026-08-17 test-wf: `pain-point-clarification-questions.md` is 1,350 characters, but the parser
# captured 712. The missing ~470 characters are **the explanatory paragraphs between** `## 모호성 1: …`
# (category) and `### Clarification Question 1` (question). The preamble does not take anything after
# the first header, and the question body starts after its header, so it went into neither.
#
# Yet that prose is **"why we are asking this"**. It is the structure upstream intended in the
# clarification question template ("Creating Clarification Questions" in `question-format-guide.md`),
# and it is what the user needs to read when the question file is rendered deterministically.
#
# Why it is a separate field rather than extending `text`: `text` is already ~200 characters and is the
# value that caused the 0.3721 incident (section above); extending it would make that comparison worse.
# The `ask`/`text` contract stays as is and only a new field is added on top.

def test_prose_between_a_category_and_a_question_becomes_context():
    md = """# 명확화 질문

서두 문단.

## 모호성 1: 두 답변이 다른 문제를 겨냥함

Question 2에서는 C를 고르셨습니다.

그런데 Question 5에서는 D를 고르셨습니다. 모순은 아니지만 확인이 필요합니다.

### Clarification Question 1
어느 쪽입니까?

A) 왼쪽
B) 오른쪽

[Answer]:
"""
    qf = parse_question_file("pain-point-clarification-questions.md", md)
    assert qf.parse_ok
    q = qf.questions[0]
    assert q.category == "모호성 1: 두 답변이 다른 문제를 겨냥함"
    # Both paragraphs are captured, and the paragraph boundary is preserved.
    assert "Question 2에서는 C를 고르셨습니다." in q.context
    assert "모순은 아니지만 확인이 필요합니다." in q.context
    assert "\n\n" in q.context, "문단 경계가 사라지면 렌더가 한 덩어리가 된다"
    # The ask/text contract is unchanged — the prose does not leak into it.
    assert q.ask == "어느 쪽입니까?"
    assert q.text == "어느 쪽입니까?"
    # The opening is still the preamble (before the first header).
    assert "서두 문단." in (qf.preamble or "")


def test_context_keeps_line_structure_so_a_table_stays_a_table():
    """There really are confirmation gate questions whose premise is a table.

    2026-08-17 test-wf `pain-point-confirmation-questions.md`: the question is "**위에
    정리한** 페인 포인트 5건이 정확합니까?" (Are the 5 pain points **summarized above** correct?), so that 5-row
    table is the question's premise. Joining lines within a paragraph with spaces yields `| # | … | |---|---| | 1 | …`, which is no longer a table."""
    md = """## 확인 대상 요약

| # | 페인 포인트 |
|---|---|
| 1 | 반복 삭감 |
| 2 | 사일로 |

## Question 1
위에 정리한 내용이 정확합니까?

A) 정확하다

[Answer]:
"""
    q = parse_question_file("pain-point-confirmation-questions.md", md).questions[0]
    lines = q.context.splitlines()
    assert lines[0].startswith("| # |")
    assert lines[1].startswith("|---")
    assert len([l for l in lines if l.startswith("|")]) == 4


def test_context_is_empty_when_a_question_follows_its_header_directly():
    """The existing file shape (7 of 8 questions) must be unchanged."""
    md = """## Question 1
질문?

A) 예
B) 아니오

[Answer]:
"""
    q = parse_question_file("x.md", md).questions[0]
    assert q.context == ""


def test_context_does_not_leak_across_categories():
    """With `## Cat A` + prose + `## Cat B` + question, that prose belongs to Cat A.

    Attaching it to the question would attach the wrong explanation, so the buffer is cleared at category headers."""
    md = """## Cat A

A 카테고리에 대한 설명.

## Cat B

### Question 1
질문?

A) 예

[Answer]:
"""
    q = parse_question_file("x.md", md).questions[0]
    assert q.category == "Cat B"
    assert "A 카테고리에 대한 설명." not in q.context


# ---- Question.ask — separating background prose from the actual question sentence ----
# 2026-08-16 ws-a-v5: the answer to `design-context.md` Q4 was not recorded. The parser takes **every
# line** before the options as the body, and that question is ordered meta (`**작성 시각**`) + 4 lines of
# background prose + the question sentence. The joined body was ~200 characters, but what went to
# AskUserQuestion was the last sentence of ~22 characters, so similarity collapsed (0.3721).
#
# This is not a coincidence but **structural**: only the question sentence goes to the tool, while the
# file also holds the background. So the parser separately exposes "the last paragraph before the options",
# and the write-back compares against that value too. `text` is kept as is for display — if the background
# disappeared from the form, the user would lose the basis for their judgment.

def test_ask_is_the_last_paragraph_before_the_options():
    md = """## Question 4 (모호성 해소 — Question 3 답변에 따른 후속)

**작성 시각**: 2026-08-16T22:49:36Z

Question 3에서 D를 선택했다. 이는 모순은 아니지만 해소해야 할 모호성을 남긴다.
25행 매트릭스는 좁은 화면에 들어가지 않으므로 별도 레이아웃이 필요하다.

모바일에서 어떤 작업까지 하실 계획입니까?

A) 열람만
X) Other (please describe after [Answer]: tag below)

[Answer]:
"""
    q = parse_question_file("design-context.md", md).questions[0]
    assert q.ask == "모바일에서 어떤 작업까지 하실 계획입니까?"
    # The display body does not lose the background.
    assert "작성 시각" in q.text and "모호성" in q.text
    assert q.ask in q.text


def test_a_wrapped_question_keeps_its_whole_paragraph_as_ask():
    """A question wrapped across lines is one paragraph — taking only the last **line** would
    leave just `누구입니까?` and make matching worse."""
    md = """## Question 1
주 사용자는
누구입니까?

A) 사내 운영팀
X) Other (please describe after [Answer]: tag below)

[Answer]:
"""
    q = parse_question_file("a.md", md).questions[0]
    assert q.ask == "주 사용자는 누구입니까?"
    assert q.ask == q.text, "문단이 하나면 ask와 text가 같다"


def test_ask_is_empty_when_the_question_has_no_body():
    """A question with only options and no body still parses (existing behavior). Its ask is then an empty string,
    and the write-back does not attempt matching with an empty value."""
    md = """## Question 1

A) 하나
X) Other (please describe after [Answer]: tag below)

[Answer]:
"""
    q = parse_question_file("a.md", md).questions[0]
    assert q.ask == ""
    assert q.text == ""


# ---- Localized question headings ----
# 2026-08-17 ws-j observation: `business-context-questions.md` was created, but no question
# card appeared. The file was perfectly fine except that the heading was in Korean:
#
#     ## 질문 1
#     비즈니스 컨텍스트 정보를 어떤 방식으로 제공하시겠습니까?
#     A) … D) … X) 기타
#     [Answer]:
#
# The regex required a literal `Question`, so it was read as 0 questions. **The regression was caused by
# a convention change**: once the question file became a user-facing artifact rather than a copy of the
# tool call, the agent localized even the headings into the project language. And since AskUserQuestion
# is now rejected, that question **disappeared entirely** — the new shape of failure after the old fallback was removed.
#
# The tolerance lives here (our parser), and the instruction to write `## Question N` lives in
# `discovery-config/CLAUDE.md`. Upstream `question-format-guide.md` is not touched — that file is the
# source of truth that prescribes `## Question [Number]`.
#
# Why an allowlist: a loose rule (`heading ending in a number`) swallows real category
# headings — `## 모호성 1` in clarification question files is exactly that.

def test_a_korean_question_heading_parses():
    md = """## 질문 1
비즈니스 컨텍스트 정보를 어떤 방식으로 제공하시겠습니까?

A) 직접 서술하겠습니다
B) URL이 있습니다
X) 기타 (아래 [Answer]: 태그 뒤에 직접 설명해 주세요)

[Answer]:
"""
    qf = parse_question_file("business-context-questions.md", md)
    assert qf.parse_ok
    assert [q.number for q in qf.questions] == [1]
    assert qf.questions[0].ask == "비즈니스 컨텍스트 정보를 어떤 방식으로 제공하시겠습니까?"
    assert [o.letter for o in qf.questions[0].options] == ["A", "B", "X"]


def test_a_korean_qualified_question_heading_parses():
    """Modifiers come along too — the counterpart of the English `Clarification Question 1`."""
    md = """### 명확화 질문 1
어느 쪽입니까?

A) 왼쪽
B) 오른쪽

[Answer]:
"""
    qf = parse_question_file("c.md", md)
    assert qf.parse_ok
    assert [q.number for q in qf.questions] == [1]


def test_a_korean_category_heading_is_not_a_question():
    """`## 모호성 1` is a category — a heading that real clarification question files use.

    Treating it as a question would mash that whole section into one question, and the real
    question underneath (`### 명확화 질문 1`) would be absorbed into it."""
    md = """## 모호성 1

설명 문단.

### 명확화 질문 1
어느 쪽입니까?

A) 왼쪽

[Answer]:
"""
    qf = parse_question_file("c.md", md)
    assert qf.parse_ok
    assert [q.number for q in qf.questions] == [1]
    assert qf.questions[0].category == "모호성 1"
    assert "설명 문단." in qf.questions[0].context


# ---- A question without options is free-form, not a parse failure ----
# 2026-08-18 observation (ws-k): Envision Step 0.2's Mode A (free description) asks without
# options. The parser read this file correctly and the defect was in frontend rendering
# (QuestionCard iterated only over the options array, so the card body was empty), but that screen is
# easy to misdiagnose as "the parser could not read it", so the contract is pinned here. If a "fix"
# that makes options mandatory comes in, this check blocks it.

_FREEFORM_MD = """# Business Context — 자유 서술 (Mode A)

## 배경

선택지가 없습니다 — `[Answer]:` 뒤에 문장으로 답해 주십시오.

## Question 1

어떤 산업 또는 비즈니스 도메인을 위한 제품입니까?

[Answer]: 

## Question 2

그 도메인에서 비즈니스의 현재 상태는 어떻습니까?

[Answer]: 
"""


def test_a_question_with_no_options_still_parses():
    qfile = parse_question_file("business-context-detail-questions.md", _FREEFORM_MD)
    assert qfile.parse_ok
    assert [q.number for q in qfile.questions] == [1, 2]
    assert all(q.options == [] for q in qfile.questions)
    # The question sentence must remain — without it only the title shows on screen.
    assert "산업" in (qfile.questions[0].text or "")


def test_free_text_round_trips_through_a_no_option_file():
    out = serialize_answers(_FREEFORM_MD, {1: "B2B SaaS 물류", 2: "운영 중"})
    again = parse_question_file("x.md", out)
    assert [q.answer for q in again.questions] == ["B2B SaaS 물류", "운영 중"]


# ---- Multi-select: the prompt layer turns a concept absent from the upstream format into a convention ----
#
# **Observed (2026-08-21, ws-i).** The question body said "(복수 선택 가능)" (multiple selections allowed),
# but the screen showed a "하나만 선택" (select one) badge and radio buttons. The user worked around it by
# typing "A, B" into the `Other — 직접 입력` field — a structured answer was demoted to free text.
#
# Cause: there was **no path at all** for `multi_select` to travel from a file question to the UI.
# In the AskUserQuestion days it came structured as a tool argument (agent/questions_payload.py),
# and when questions moved to files, only that value was left behind. The frontend is already ready —
# QuestionCard draws checkboxes for `multi_select` and joins letters with commas.
#
# Upstream `question-format-guide.md` does not have this concept (it prescribes only single selection,
# `[Answer]: C`). So this follows **the same division** the top of this file records for heading
# tolerance: tolerance in the parser, the instruction to write it that way in `discovery-config/CLAUDE.md`,
# and upstream left untouched.

_MULTI_MD = """## Question 1
보유한 콘텐츠 자산은 어디까지입니까? (복수 선택 가능)

A) 패키지 상품 상세
B) 고객 여행 후기
X) Other — 직접 입력

[Answer]:

## Question 2
주력 채널은 무엇입니까?

A) 웹
B) 앱

[Answer]:
"""


def test_a_parenthesised_multi_select_marker_sets_the_flag():
    qf = parse_question_file("x.md", _MULTI_MD)
    assert qf.questions[0].multi_select is True


def test_a_question_without_the_marker_stays_single_select():
    """The default must be single selection — getting it wrong the other way turns every question into checkboxes."""
    qf = parse_question_file("x.md", _MULTI_MD)
    assert qf.questions[1].multi_select is False


def test_the_english_marker_is_recognised():
    md = _MULTI_MD.replace("(복수 선택 가능)", "(select all that apply)")
    assert parse_question_file("x.md", md).questions[0].multi_select is True


def test_the_marker_must_be_parenthesised():
    """Why parentheses are required: some questions **ask about** multi-select itself. Without parentheses,
    "복수 선택 UI가 필요합니까?" (Do you need a multi-select UI?) would render as checkboxes."""
    md = _MULTI_MD.replace("(복수 선택 가능)", "— 복수 선택 UI가 필요합니까?")
    assert parse_question_file("x.md", md).questions[0].multi_select is False


def test_the_marker_survives_answer_round_trip():
    """The write-back does not touch the body, so the marker must survive — if it were lost, the same
    question would turn into radio buttons in the second round."""
    out = serialize_answers(_MULTI_MD, {1: "A,B"})
    again = parse_question_file("x.md", out)
    assert again.questions[0].multi_select is True
    assert again.questions[0].answer == "A,B"


# `discovery-config/CLAUDE.md` must be **language-neutral** (all projects share it —
# Korean prose itself is a language signal, see test_workspace_rules's
# `test_shared_config_dirs_have_no_language_directive`), so Korean expressions cannot be baked into
# the instructions. The instruction says only "a parenthetical note meaning 'select all that apply' in the
# project language", and the model picks the actual wording. So the parser has to absorb that range — the
# fact that the UI's own badge is "여러 개 선택 가능" is itself evidence of that range.

def _one_q(marker: str) -> str:
    return f"## Question 1\n보유 자산은 어디까지입니까? {marker}\n\nA) 가\nB) 나\n\n[Answer]:\n"


def test_the_korean_wordings_the_model_actually_picks_are_recognised():
    for marker in ("(복수 선택 가능)",
                   "(중복 선택 가능)",
                   "(여러 개 선택 가능)",
                   "(여러개 선택 가능)",
                   "(해당하는 것 모두 선택)",
                   "(모두 선택)"):
        qf = parse_question_file("x.md", _one_q(marker))
        assert qf.questions[0].multi_select is True, marker


def test_a_parenthesised_single_select_clarifier_is_not_multi():
    """"여러 개 중 하나만 선택" (select only one of several) is wording that **emphasizes** single selection.
    Counting words alone would turn it into checkboxes, letting the user pick several where they should pick one."""
    for marker in ("(여러 개 중 하나만 선택)",
                   "(하나만 선택)",
                   "(pick one)"):
        qf = parse_question_file("x.md", _one_q(marker))
        assert qf.questions[0].multi_select is False, marker


def test_the_english_wordings_are_recognised():
    for marker in ("(select all that apply)",
                   "(choose all that apply)",
                   "(multiple selections allowed)"):
        qf = parse_question_file("x.md", _one_q(marker))
        assert qf.questions[0].multi_select is True, marker


def test_options_past_f_are_kept():
    """The upstream guide goes up to F, but the model writes `G)` (observed). While only up to F was accepted,
    that line was caught neither as an option nor as body text and vanished."""
    md = """# Questions

## Question 1
어떤 방식으로 해결하고 있습니까? (해당하는 것을 모두 선택하세요)

A) 하나
B) 둘
C) 셋
D) 넷
E) 다섯
F) 여섯
G) 일곱
X) Other (please describe after [Answer]: tag below)

[Answer]:
"""
    q = parse_question_file("q.md", md).questions[0]
    assert [o.letter for o in q.options] == ["A", "B", "C", "D", "E", "F", "G", "X"]
    assert q.multi_select


# ---- Translated free-input options ----
#
# Agents in ko projects rewrite the upstream template's `X) Other (please describe after [Answer]: tag below)`,
# changing even the label and letter. Observed (ws-h): all 61 "other" options had the shape
# `D) 기타 (아래 [Answer]: 태그 뒤에 설명해 주세요)` and were read as ordinary options, so in multi-select
# choosing "기타" (Other) left no field to write in.

_KO_OTHER = """# Questions

## Question 1
현재 쓰는 도구는 무엇입니까? (해당하는 항목 모두 선택)

A) 엑셀
B) 그룹웨어 결재
C) 기타 행정 업무 시스템
D) 기타 (아래 [Answer]: 태그 뒤에 설명해 주세요)

[Answer]:

## Question 2
제품 이름은?

A) 안전ON
B) 기타
C) 수정이 필요하다 (아래 [Answer]: 태그 뒤에 고칠 부분을 적어 주세요)

[Answer]:
"""


def test_a_translated_other_option_is_a_write_in_even_without_the_letter_x():
    qf = parse_question_file("x.md", _KO_OTHER)
    first, second = qf.questions
    assert first.multi_select is True
    assert [(o.letter, o.is_other) for o in first.options] == [
        ("A", False), ("B", False),
        ("C", False),   # "기타 행정 업무 시스템" is a real option — it merely starts with the word "기타"
        ("D", True)]
    # The single word "기타" is also free input. An option that asks you to elaborate in [Answer] is a decision by being chosen —
    # turning it into free input would lose what was chosen from the answer.
    assert [(o.letter, o.is_other) for o in second.options] == [
        ("A", False), ("B", True), ("C", False)]


def test_the_payload_path_uses_the_same_rule():
    """If the two paths diverge, the same question renders differently depending on its input format (file vs. ask_questions)."""
    from aipds.agent.questions_payload import normalize_questions_payload
    out = normalize_questions_payload({"name": "x", "questions": [{
        "number": 1, "text": "도구는?", "multi_select": True,
        "options": [{"letter": "A", "text": "엑셀"},
                    {"letter": "D", "text": "기타 (아래 [Answer]: 태그 뒤에 설명해 주세요)"},
                    {"letter": "E", "text": "수정하겠다 ([Answer]: 뒤에 적어 주세요)"}]}]})
    assert [o["is_other"] for o in out["questions"][0]["options"]] == [False, True, False]


# ---- An answer is a block: multi-line elaborations survive the round trip ----
#
# The elaboration and Other fields are textareas, so `A: <paragraphs of context>` is ordinary
# input. The file kept all of it, but the parser read only the `[Answer]:` line — the reopened
# card and the handoff ledger saw the first line, and re-answering left the old answer's other
# lines behind as orphans.

_TWO_MD = """\
## Question 1
어떤 방식으로 진행할까요?

A) 처음부터
B) 기존 자료로
X) Other (please describe after [Answer]: tag below)

[Answer]:

## Question 2
대상 사용자는 누구입니까?

A) 내부 직원
B) 외부 고객

[Answer]:
"""

_LONG = "A: 첫 줄\n둘째 줄\n\n셋째 문단 — 아주 긴 맥락"


def test_a_multi_line_answer_round_trips_whole():
    out = serialize_answers(_TWO_MD, {1: _LONG, 2: "B"})
    again = parse_question_file("x-questions.md", out)
    assert [q.answer for q in again.questions] == [_LONG, "B"]


def test_re_answering_replaces_the_whole_previous_block():
    first = serialize_answers(_TWO_MD, {1: _LONG})
    out = serialize_answers(first, {1: "B"})
    assert "둘째 줄" not in out and "셋째 문단" not in out
    assert out == serialize_answers(_TWO_MD, {1: "B"})


def test_lines_that_look_like_structure_stay_inside_the_answer():
    tricky = "A: 메모\n## 이건 헤딩이 아니다\n---\n```\n[Answer]: 이것도 답의 일부\nB) 보기도 아니다"
    out = serialize_answers(_TWO_MD, {1: tricky, 2: "A"})
    again = parse_question_file("x-questions.md", out)
    assert [q.number for q in again.questions] == [1, 2]
    assert again.questions[0].answer == tricky
    assert [o.letter for o in again.questions[0].options] == ["A", "B", "X"]
    assert again.questions[1].answer == "A"


def test_an_answer_written_before_the_fix_is_read_whole():
    # The old writer put continuation lines in unindented — the shape real files now hold.
    legacy = _TWO_MD.replace("[Answer]:\n\n## Question 2",
                             "[Answer]: A: 첫 줄\n둘째 줄\n\n셋째 줄\n\n## Question 2", 1)
    qf = parse_question_file("x-questions.md", legacy)
    assert qf.questions[0].answer == "A: 첫 줄\n둘째 줄\n\n셋째 줄"
    assert qf.questions[1].answer is None


def test_a_block_stops_at_a_thematic_break():
    md = _TWO_MD.replace("[Answer]:\n\n## Question 2",
                         "[Answer]: A\n\n---\n\n자유 서술\n\n## Question 2", 1)
    assert parse_question_file("x-questions.md", md).questions[0].answer == "A"


def test_an_empty_tag_does_not_absorb_the_text_after_it():
    # Reading that text as an answer would mark the question answered and its card would never appear.
    md = _TWO_MD.replace("[Answer]:\n\n## Question 2",
                         "[Answer]:\n에이전트가 덧붙인 설명\n\n## Question 2", 1)
    assert parse_question_file("x-questions.md", md).questions[0].answer is None


def test_a_multi_line_answer_at_end_of_file_keeps_the_file_ending():
    out = serialize_answers(_TWO_MD.rstrip("\n"), {2: "A: 첫 줄\n둘째 줄"})
    assert out.endswith("[Answer]: A: 첫 줄\n  둘째 줄")
    assert parse_question_file("x-questions.md", out).questions[1].answer == "A: 첫 줄\n둘째 줄"


def test_crlf_files_keep_their_endings():
    crlf = _TWO_MD.replace("\n", "\r\n")
    out = serialize_answers(crlf, {1: "A: 첫 줄\n둘째 줄"})
    assert "[Answer]: A: 첫 줄\r\n  둘째 줄\r\n" in out
    assert "\n" not in out.replace("\r\n", "")
