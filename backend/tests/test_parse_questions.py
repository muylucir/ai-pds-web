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


# ---- 문항 헤더 앞에 붙는 수식어, 그리고 해시 4개 ----
# 2026-08-17 test-wf: 질문 파일 8개 중 `pain-point-clarification-questions.md`가
# **문항 0개**로 읽혀 raw markdown 폴백으로 떨어졌고, 그 파일의 답변은 기록되지
# 않았다(`[Answer]` 슬롯 1개, 채워짐 0개). 정규식이 해시 바로 뒤에 `Question`이
# 오기를 요구했는데 그 파일은 이렇게 쓴다:
#
#   ### Clarification Question 1
#
# **상류가 규정한 형식이다.** `question-format-guide.md`는 `## Question [Number]`
# (22행)와 `### Clarification Question 1`(223행, "Creating Clarification
# Questions") 두 가지를 모두 템플릿으로 싣는다. 룰셋 전체에는 해시 4개짜리
# (`#### Question 1: Brand & Design Context`)도 있다.
#
# 번호가 여전히 유일한 판별자다: 수식어를 허용해도 `### Question File Format`이나
# `### Context Questions (Per Use Case)`는 번호가 없어 걸리지 않는다. 수식어는
# **한 단어**만 허용한다 — `## Answer to Question 3` 같은 참조용 산문 헤딩까지
# 삼키면 그 절이 한 문항으로 뭉개진다(위 테스트가 막는 것과 같은 실패).

def test_a_qualified_question_heading_parses():
    """`### Clarification Question 1` — 상류 question-format-guide.md:223의 형식."""
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
    """`#### Question 1: Brand & Design Context` — 룰셋에 실제로 있는 형태."""
    md = """#### Question 1: Brand & Design Context
어떤 브랜드 톤을 원하십니까?

A) 차분한
B) 대담한

[Answer]:
"""
    qf = parse_question_file("design-context.md", md)
    assert qf.parse_ok
    assert [q.number for q in qf.questions] == [1]
    # 콜론 뒤 제목은 헤더의 접미사이지 본문이 아니다(접미사 테스트와 같은 계약).
    assert qf.questions[0].ask == "어떤 브랜드 톤을 원하십니까?"


def test_a_heading_that_names_question_without_a_number_is_still_not_a_question():
    """수식어를 허용해도 번호가 없으면 문항이 아니다.

    전부 룰셋에 실재하는 헤딩이다 — 이것들이 문항으로 잡히면 그 절 전체가
    한 문항으로 뭉개진다."""
    for heading in ("### Question File Format",
                    "### Context Questions (Per Use Case)",
                    "## MANDATORY: Question File Format",
                    "### ⛔ GATE: Await PRFAQ Clarifying Question Answers",
                    # 수식어는 한 단어만 — 참조용 산문 헤딩은 삼키지 않는다.
                    "## Answer to Question 3"):
        qf = parse_question_file("x.md", heading + "\n\n본문\n")
        assert not qf.parse_ok, heading


def test_answers_are_written_back_to_a_qualified_heading():
    """파싱만으로는 부족하다 — 되기록까지 돌아야 이 결함이 닫힌다.

    2026-08-17 test-wf에서 잃은 것은 파싱 결과가 아니라 **답변**이었다.
    `serialize_answers`는 번호로 슬롯을 찾으므로 헤더 인식이 곧 되기록이다."""
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
    # 되읽어도 같은 번호로 잡혀야 한다(파서와 직렬화가 같은 기준을 쓴다는 불변식).
    qf = parse_question_file("c.md", out)
    assert [(q.number, q.answer) for q in qf.questions] == [(1, "B"), (2, "A")]


# ---- Question.context — 카테고리 헤더와 문항 헤더 사이의 산문 ----
# 2026-08-17 test-wf: `pain-point-clarification-questions.md`가 1,350자인데 파서가
# 붙잡은 것은 712자였다. 사라진 ~470자는 `## 모호성 1: …`(카테고리)와
# `### Clarification Question 1`(문항) **사이의 설명 문단들**이다. preamble은 첫
# 헤더 뒤를 받지 않고 문항 본문은 헤더 뒤부터 시작하므로 어느 쪽에도 안 들어갔다.
#
# 그런데 그 산문이 **"왜 이걸 묻는가"**다. 상류가 명확화 질문 템플릿
# (`question-format-guide.md`의 "Creating Clarification Questions")에서 의도한
# 구조이고, 질문 파일을 결정론적으로 렌더할 때 사용자가 읽어야 하는 내용이다.
#
# `text`를 늘리지 않고 별 필드로 두는 이유: `text`는 이미 ~200자여서 0.3721
# 사고를 낸 값이고(위 절), 더 늘리면 그 비교가 더 나빠진다. `ask`/`text` 계약은
# 그대로 두고 새 필드만 얹는다.

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
    # 두 문단이 모두 잡히고, 문단 경계가 남는다.
    assert "Question 2에서는 C를 고르셨습니다." in q.context
    assert "모순은 아니지만 확인이 필요합니다." in q.context
    assert "\n\n" in q.context, "문단 경계가 사라지면 렌더가 한 덩어리가 된다"
    # ask/text 계약은 그대로 — 산문이 그쪽으로 새지 않는다.
    assert q.ask == "어느 쪽입니까?"
    assert q.text == "어느 쪽입니까?"
    # 서두는 여전히 preamble이다(첫 헤더 앞).
    assert "서두 문단." in (qf.preamble or "")


def test_context_keeps_line_structure_so_a_table_stays_a_table():
    """확인 게이트 질문의 전제가 표인 경우가 실제로 있다.

    2026-08-17 test-wf `pain-point-confirmation-questions.md`: 질문이 "**위에
    정리한** 페인 포인트 5건이 정확합니까?"라서 그 5행 표가 질문의 전제다. 문단
    안을 공백으로 이으면 `| # | … | |---|---| | 1 | …`이 되어 표가 아니게 된다."""
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
    """기존 파일 모양(문항 8개 중 7개)에서는 변화가 없어야 한다."""
    md = """## Question 1
질문?

A) 예
B) 아니오

[Answer]:
"""
    q = parse_question_file("x.md", md).questions[0]
    assert q.context == ""


def test_context_does_not_leak_across_categories():
    """`## Cat A` + 산문 + `## Cat B` + 문항이면 그 산문은 Cat A 것이다.

    문항에 붙이면 엉뚱한 설명이 붙으므로 카테고리 헤더에서 버퍼를 비운다."""
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


# ---- Question.ask — 배경 산문과 실제 질문 문장을 나눈다 ----
# 2026-08-16 keumkang-v5: `design-context.md` Q4의 답변이 기록되지 않았다. 파서가
# 옵션 앞의 **모든 줄**을 본문으로 잡는데, 그 문항은 메타(`**작성 시각**`) + 배경
# 산문 4줄 + 질문 문장 순서다. 조인된 본문이 ~200자인데 AskUserQuestion에 간 것은
# 마지막 문장 ~22자여서 유사도가 무너졌다(0.3721).
#
# 이건 우연이 아니라 **구조적**이다: 도구에는 질문 문장만 가고 파일은 배경까지 담는다.
# 그래서 파서가 "옵션 직전 마지막 문단"을 따로 노출하고, 되기록이 그 값과도 비교한다.
# `text`는 표시용으로 그대로 둔다 — 배경이 폼에서 사라지면 사용자가 판단 근거를 잃는다.

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
    # 표시용 본문은 배경을 잃지 않는다.
    assert "작성 시각" in q.text and "모호성" in q.text
    assert q.ask in q.text


def test_a_wrapped_question_keeps_its_whole_paragraph_as_ask():
    """줄바꿈으로 감긴 질문은 한 문단이다 — 마지막 **줄**만 취하면
    `누구입니까?`만 남아 매칭이 더 나빠진다."""
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
    """본문 없이 옵션만 있는 문항도 파싱은 된다(기존 동작). 그때 ask는 빈 문자열이고,
    되기록은 빈 값으로 매칭을 시도하지 않는다."""
    md = """## Question 1

A) 하나
X) Other (please describe after [Answer]: tag below)

[Answer]:
"""
    q = parse_question_file("a.md", md).questions[0]
    assert q.ask == ""
    assert q.text == ""


# ---- 지역화된 문항 헤딩 ----
# 2026-08-17 sarang-hpt 실측: `business-context-questions.md`가 만들어졌는데 질문
# 카드가 뜨지 않았다. 파일은 완전히 정상인데 헤딩만 한국어였다:
#
#     ## 질문 1
#     비즈니스 컨텍스트 정보를 어떤 방식으로 제공하시겠습니까?
#     A) … D) … X) 기타
#     [Answer]:
#
# 정규식이 리터럴 `Question`을 요구해 문항 0개로 읽혔다. **회귀의 원인은 규약
# 변경이다**: 질문 파일이 도구 호출의 사본이 아니라 사용자용 산출물이 되면서
# 에이전트가 헤딩까지 프로젝트 언어로 지역화했다. 그리고 AskUserQuestion이 이제
# 거부되므로 그 질문은 **완전히 사라졌다** — 옛 폴백이 없어진 뒤의 새 실패 모양이다.
#
# 관용은 여기(우리 파서)에 두고, `## Question N`을 쓰라는 지시는
# `discovery-config/CLAUDE.md`에 둔다. 상류 `question-format-guide.md`는 건드리지
# 않는다 — 그 파일이 `## Question [Number]`를 규정한 정본이다.
#
# 허용목록으로 하는 이유: 느슨한 규칙(`숫자로 끝나는 헤딩`)은 실재하는 카테고리
# 헤딩을 삼킨다 — 명확화 질문 파일의 `## 모호성 1`이 그것이다.

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
    """수식어도 같이 온다 — 영어 쪽 `Clarification Question 1`의 대응."""
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
    """`## 모호성 1`은 카테고리다 — 실제 명확화 질문 파일이 쓰는 헤딩이다.

    이것을 문항으로 삼으면 그 절 전체가 한 문항으로 뭉개지고, 그 아래의 진짜
    문항(`### 명확화 질문 1`)이 그 안에 흡수된다."""
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


# ---- 보기 없는 문항은 주관식이지 파싱 실패가 아니다 ----
# 2026-08-18 실측(123456test): Envision Step 0.2의 Mode A(자유 서술)가 선택지 없이
# 묻는다. 파서는 이 파일을 정확히 읽었고 결함은 프론트 렌더링이었지만
# (QuestionCard가 보기 배열만 순회해 카드 본문이 비었다), 그 화면을 "파서가 못
# 읽었다"로 오진하기 쉬우므로 여기서 계약을 고정한다. 보기를 필수로 만드는
# "수정"이 들어오면 이 검사가 막는다.

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
    # 질문 문장이 남아야 한다 — 없으면 화면에 제목만 뜬다.
    assert "산업" in (qfile.questions[0].text or "")


def test_free_text_round_trips_through_a_no_option_file():
    out = serialize_answers(_FREEFORM_MD, {1: "B2B SaaS 물류", 2: "운영 중"})
    again = parse_question_file("x.md", out)
    assert [q.answer for q in again.questions] == ["B2B SaaS 물류", "운영 중"]


# ---- 복수 선택: 상류 형식에 없는 개념을 프롬프트 레이어가 규약으로 만든다 ----
#
# **실측(2026-08-21, 하나투어 프로젝트).** 문항 본문이 "(복수 선택 가능)"이라고 적혀
# 있는데 화면에는 "하나만 선택" 배지와 라디오 버튼이 떴다. 사용자는 `Other — 직접 입력`
# 칸에 "A, B"라고 써서 우회했다 — 구조화된 답변이 자유 텍스트로 격하됐다.
#
# 원인: `multi_select`가 파일 질문에서 UI까지 오는 경로가 **아예 없었다.**
# AskUserQuestion 시절에는 도구 인자로 구조화돼 왔고(agent/questions_payload.py),
# 질문이 파일로 옮겨가면서 그 값만 남겨졌다. 프론트는 이미 준비돼 있다 —
# QuestionCard가 `multi_select`로 체크박스를 그리고 letter를 콤마로 잇는다.
#
# 상류 `question-format-guide.md`에는 이 개념이 없다(`[Answer]: C` 단일 선택만
# 규정한다). 그래서 이 파일 상단이 헤딩 관용에 대해 적어 둔 것과 **같은 분담**을
# 따른다: 관용은 파서에, 그렇게 쓰라는 지시는 `discovery-config/CLAUDE.md`에, 상류는
# 건드리지 않는다.

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
    """기본값이 단일 선택이어야 한다 — 반대로 틀리면 모든 문항이 체크박스가 된다."""
    qf = parse_question_file("x.md", _MULTI_MD)
    assert qf.questions[1].multi_select is False


def test_the_english_marker_is_recognised():
    md = _MULTI_MD.replace("(복수 선택 가능)", "(select all that apply)")
    assert parse_question_file("x.md", md).questions[0].multi_select is True


def test_the_marker_must_be_parenthesised():
    """괄호를 요구하는 이유: 복수 선택 **자체를 묻는** 문항이 있다. 괄호가 없으면
    "복수 선택 UI가 필요합니까?"가 체크박스로 렌더된다."""
    md = _MULTI_MD.replace("(복수 선택 가능)", "— 복수 선택 UI가 필요합니까?")
    assert parse_question_file("x.md", md).questions[0].multi_select is False


def test_the_marker_survives_answer_round_trip():
    """되기록이 본문을 건드리지 않으므로 표시가 살아 있어야 한다 — 죽으면 두 번째
    라운드에서 같은 문항이 라디오로 바뀐다."""
    out = serialize_answers(_MULTI_MD, {1: "A,B"})
    again = parse_question_file("x.md", out)
    assert again.questions[0].multi_select is True
    assert again.questions[0].answer == "A,B"


# `discovery-config/CLAUDE.md`는 **언어 중립**이어야 하므로(전 프로젝트가 공유한다 —
# 한글 산문 자체가 언어 신호다, test_workspace_rules의
# `test_shared_config_dirs_have_no_language_directive`) 한국어 표현을 지시문에 박을 수
# 없다. 지시는 "프로젝트 언어로 '모두 선택' 뜻의 괄호 주석"까지만 말하고, 실제 문구는
# 모델이 고른다. 그래서 파서가 그 폭을 감당해야 한다 — UI 자신의 배지가
# "여러 개 선택 가능"인 것부터가 그 폭의 증거다.

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
    """"여러 개 중 하나만 선택"은 단일 선택을 **강조**하는 문구다. 낱말만 세면
    그것이 체크박스가 되고, 사용자는 골라야 할 하나를 여러 개 고를 수 있게 된다."""
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
