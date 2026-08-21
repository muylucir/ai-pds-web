# backend/tests/test_answer_summary.py — 제출된 답변을 사람이 읽는 텍스트로.
#
# **이 검사들은 frontend/lib/answerSummary.test.ts에서 옮겨 왔다(2026-08-21).**
# 판별이 프론트에만 있었던 것이 결함의 뿌리였다: 사용자가 화면에서 본 말풍선은
# 브라우저 안에서만 만들어졌고 서버로 간 적이 없다. 서버는 자기가 만든 다른 문장을
# 트랜스크립트에 기록했고, 그래서 새로고침하면 대화가 기계 문구로 보였다.
#
# 판별을 백엔드로 옮기면 표현이 하나가 되고 갈라질 수 없다. 옮기면서 얻는 것이 하나
# 더 있다: 이 텍스트는 채팅 말풍선이므로 **프로젝트 언어**를 따라야 하는데
# (agent/prompts.py 헤더, lib/approvalMarker.ts의 같은 판단) 프론트에 있던 동안은 UI
# 언어를 따랐다.
#
# 되돌려야 할 계약은 QuestionCard의 값 규약이다 — 한 문자열에 네 모양이 담긴다:
# 맨 letter("A"), 부연이 붙은 letter("A: 부연"), 복수 선택의 콤마 결합("A,C"),
# 그리고 letter처럼 시작하는 자유 텍스트("Broker: …"). 앞의 셋만 펼칠 수 있고,
# **그것을 가려내는 것이 이 모듈의 일 전부**다.
from aipds.answer_summary import answer_summary
from aipds.models import Question, QuestionFile, QuestionOption


def opt(letter: str, text: str, is_other: bool = False) -> QuestionOption:
    return QuestionOption(letter=letter, text=text, is_other=is_other,
                          recommended=False)


def q(number: int, text: str, options: list[QuestionOption] | None = None,
      multi_select: bool = False) -> Question:
    return Question(number=number, category=None, text=text,
                    options=options or [], answer=None,
                    multi_select=multi_select)


def qfile(questions: list[Question]) -> QuestionFile:
    return QuestionFile(name="q.md", preamble=None, questions=questions,
                        parse_ok=True, raw_markdown=None)


def test_pairs_each_answer_with_the_question_it_answers():
    f = qfile([q(1, "주 사용자는 누구입니까?"), q(2, "출시 목표 시점은?")])
    assert answer_summary(f, {1: "사내 QA 담당자", 2: "2개월 이내"}, "ko") == (
        "Q1. 주 사용자는 누구입니까?\n→ 사내 QA 담당자\n\n"
        "Q2. 출시 목표 시점은?\n→ 2개월 이내")


def test_expands_an_option_letter_to_its_text():
    """폼이 제출하는 것은 letter("A")뿐이고 그것만으로는 읽는 사람에게 아무 뜻이
    없다 — 질문 폼을 다시 열지 않아도 대화가 이해되는 것이 이 모듈의 목적이다."""
    f = qfile([q(1, "어떤 방식이 좋습니까?",
                 [opt("A", "기존 도구 확장"), opt("B", "신규 개발")])])
    assert answer_summary(f, {1: "A"}, "ko") == (
        "Q1. 어떤 방식이 좋습니까?\n→ A. 기존 도구 확장")


def test_keeps_the_note_attached_to_a_letter_note_answer():
    """QuestionCard의 값 규약: 부연이 붙은 선택은 "A: 부연" 한 문자열로 온다.
    양쪽이 다 의미를 갖는다."""
    f = qfile([q(1, "어떤 방식이 좋습니까?",
                 [opt("A", "기존 도구 확장"), opt("B", "신규 개발")])])
    assert answer_summary(f, {1: "A: 단 인증만 새로"}, "ko") == (
        "Q1. 어떤 방식이 좋습니까?\n→ A. 기존 도구 확장 — 단 인증만 새로")


def test_expands_every_letter_of_a_comma_joined_multi_select_answer():
    f = qfile([q(1, "필요한 기능을 고르세요",
                 [opt("A", "자동 생성"), opt("B", "수동 편집"),
                  opt("C", "이력 관리")], multi_select=True)])
    assert answer_summary(f, {1: "A,C"}, "ko") == (
        "Q1. 필요한 기능을 고르세요\n→ A. 자동 생성, C. 이력 관리")


def test_keeps_free_text_whose_first_token_merely_looks_like_a_letter():
    """QuestionCard가 값에서 Other 모드를 **추론하지 않는** 이유가 이 경우다.
    ": "로 쪼개면 살리려던 답변을 훼손한다."""
    f = qfile([q(1, "다른 의견이 있으면 적어주세요", [opt("A", "없음")])])
    assert answer_summary(f, {1: "Broker: 큐를 따로 두고 싶다"}, "ko") == (
        "Q1. 다른 의견이 있으면 적어주세요\n→ Broker: 큐를 따로 두고 싶다")


def test_passes_an_other_options_own_text_through_without_expanding_it():
    """is_other 보기의 letter는 읽는 사람에게 필요한 라벨이 아니다 — 타이핑된
    텍스트가 곧 답변이다."""
    f = qfile([q(1, "어떤 방식이 좋습니까?",
                 [opt("A", "기존 도구 확장"), opt("X", "", is_other=True)])])
    assert answer_summary(f, {1: "직접 만든 스크립트로"}, "ko") == (
        "Q1. 어떤 방식이 좋습니까?\n→ 직접 만든 스크립트로")


def test_skips_questions_the_user_left_blank():
    f = qfile([q(1, "첫 질문"), q(2, "두 번째 질문")])
    assert answer_summary(f, {1: "답", 2: ""}, "ko") == "Q1. 첫 질문\n→ 답"


def test_an_answer_with_no_matching_question_is_appended_not_dropped():
    """방어적: 낡은 폼이나 서버측 번호 재부여로 문항 목록이 설명하지 못하는 키가
    올 수 있다. 답변을 잃는 것이 질문 없이 보여주는 것보다 나쁘다."""
    f = qfile([q(1, "아는 질문")])
    assert answer_summary(f, {1: "답", 9: "고아 답변"}, "ko") == (
        "Q1. 아는 질문\n→ 답\n\nQ9.\n→ 고아 답변")


def test_returns_a_marker_rather_than_an_empty_bubble():
    """빈 문자열은 빈 말풍선으로 렌더된다 — 이 모듈이 막으려는 바로 그것이다."""
    f = qfile([q(1, "질문")])
    assert answer_summary(f, {}, "ko") == "답변 제출"
    assert answer_summary(f, {1: "   "}, "ko") == "답변 제출"


def test_the_empty_marker_follows_the_project_language():
    """말풍선으로 남는 대화 텍스트다 — UI 언어가 아니라 프로젝트 언어다. 프론트에
    있던 동안은 이것이 UI 언어를 따랐고, 그 불일치가 백엔드로 옮기며 사라진다."""
    f = qfile([q(1, "질문")])
    out = answer_summary(f, {}, "en")
    assert out == "Answers submitted", out


def test_the_question_order_wins_over_the_answers_key_order():
    """`answers`의 키 순서는 프론트가 보낸 JSON 순서다. 사용자가 문항을 건너뛰며
    답하면 그 순서가 화면과 어긋나므로 문항 목록 순서를 따른다."""
    f = qfile([q(1, "첫"), q(2, "둘"), q(12, "열둘")])
    out = answer_summary(f, {12: "C", 2: "B", 1: "A"}, "ko")
    assert out.index("Q1.") < out.index("Q2.") < out.index("Q12.")
