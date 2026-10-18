# backend/tests/test_answer_summary.py — submitted answers as human-readable text.
#
# **These checks were moved from frontend/lib/answerSummary.test.ts (2026-08-21).**
# The root of the defect was that the classification existed only on the frontend: the bubble the user saw
# on screen was built only inside the browser and never went to the server. The server recorded a different
# sentence of its own in the transcript, so after a refresh the conversation looked like machine wording.
#
# Moving the classification to the backend leaves a single representation that cannot diverge. There is one
# more gain from the move: this text is a chat bubble, so it must follow the **project language**
# (agent/prompts.py header, the same judgment in lib/approvalMarker.ts), but while it lived on the frontend
# it followed the UI language.
#
# The contract to reverse is QuestionCard's value convention — one string holds four shapes:
# a bare letter ("A"), a letter with an elaboration ("A: 부연"), a comma join for multi-select ("A,C"),
# and free text that starts like a letter ("Broker: …"). Only the first three can be expanded, and
# **telling them apart is this module's entire job**.
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
    """All the form submits is a letter ("A"), and that alone means nothing to the reader —
    the purpose of this module is for the conversation to be understandable without reopening the question form."""
    f = qfile([q(1, "어떤 방식이 좋습니까?",
                 [opt("A", "기존 도구 확장"), opt("B", "신규 개발")])])
    assert answer_summary(f, {1: "A"}, "ko") == (
        "Q1. 어떤 방식이 좋습니까?\n→ A. 기존 도구 확장")


def test_keeps_the_note_attached_to_a_letter_note_answer():
    """QuestionCard's value convention: a choice with an elaboration arrives as the single string "A: 부연".
    Both sides carry meaning."""
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
    """This case is why QuestionCard **does not infer** Other mode from the value.
    Splitting on ": " would damage the answer we meant to preserve."""
    f = qfile([q(1, "다른 의견이 있으면 적어주세요", [opt("A", "없음")])])
    assert answer_summary(f, {1: "Broker: 큐를 따로 두고 싶다"}, "ko") == (
        "Q1. 다른 의견이 있으면 적어주세요\n→ Broker: 큐를 따로 두고 싶다")


def test_passes_an_other_options_own_text_through_without_expanding_it():
    """The letter of an is_other option is not a label the reader needs — the typed
    text is the answer."""
    f = qfile([q(1, "어떤 방식이 좋습니까?",
                 [opt("A", "기존 도구 확장"), opt("X", "", is_other=True)])])
    assert answer_summary(f, {1: "직접 만든 스크립트로"}, "ko") == (
        "Q1. 어떤 방식이 좋습니까?\n→ 직접 만든 스크립트로")


def test_skips_questions_the_user_left_blank():
    f = qfile([q(1, "첫 질문"), q(2, "두 번째 질문")])
    assert answer_summary(f, {1: "답", 2: ""}, "ko") == "Q1. 첫 질문\n→ 답"


def test_an_answer_with_no_matching_question_is_appended_not_dropped():
    """Defensive: a stale form or server-side renumbering can send keys the question list cannot
    explain. Losing the answer is worse than showing it without the question."""
    f = qfile([q(1, "아는 질문")])
    assert answer_summary(f, {1: "답", 9: "고아 답변"}, "ko") == (
        "Q1. 아는 질문\n→ 답\n\nQ9.\n→ 고아 답변")


def test_returns_a_marker_rather_than_an_empty_bubble():
    """An empty string renders as an empty bubble — exactly what this module is meant to prevent."""
    f = qfile([q(1, "질문")])
    assert answer_summary(f, {}, "ko") == "답변 제출"
    assert answer_summary(f, {1: "   "}, "ko") == "답변 제출"


def test_the_empty_marker_follows_the_project_language():
    """This is conversation text that remains as a bubble — the project language, not the UI language. While it lived on the
    frontend it followed the UI language, and that mismatch disappears with the move to the backend."""
    f = qfile([q(1, "질문")])
    out = answer_summary(f, {}, "en")
    assert out == "Answers submitted", out


def test_the_question_order_wins_over_the_answers_key_order():
    """The key order of `answers` is the JSON order the frontend sent. When the user skips around
    while answering, that order diverges from the screen, so the question list order is followed."""
    f = qfile([q(1, "첫"), q(2, "둘"), q(12, "열둘")])
    out = answer_summary(f, {12: "C", 2: "B", 1: "A"}, "ko")
    assert out.index("Q1.") < out.index("Q2.") < out.index("Q12.")
