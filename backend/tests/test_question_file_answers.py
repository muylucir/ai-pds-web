# backend/tests/test_question_file_answers.py — 답변이 질문 파일에 되기록되는가.
#
# 왜 이 파일이 생겼는가: Pathfinder는 질문을 AskUserQuestion으로 전달하면서
# 질문 파일의 `[Answer]:` 칸을 영구히 비워 뒀다. ai-plc 워크플로우는 그 칸이
# 채워지는 것을 전제로 돌아간다 — aws-aiplc-rule-details/common/question-format-guide.md
# 의 "Read the question file / Extract answers after [Answer]: tags"이고,
# session-continuity.md:31-33은 세션 재개 때 `strategy-questions.md`를 **읽으라고**
# 지시한다. 칸이 비어 있으면 재개한 세션은 사용자의 결정을 잃는다.
#
# 되기록을 **번호가 아니라 질문 텍스트로** 맞추는 이유가 이 테스트의 핵심이다.
# AskUserQuestion은 질문 4개 × 보기 4개가 스키마 하드 리밋이므로 10문항 파일은
# 4+4+2 세 라운드로 쪼개지고, 각 라운드의 문항 번호는 1부터 다시 시작한다.
# 번호로 맞추면 라운드 2의 답이 문항 1~4에 덮여 조용히 오염된다.
from __future__ import annotations

from pathlib import Path

from pathfinder.agent.question_file_answers import record_answers
from pathfinder.parsers.questions import parse_question_file

#: 10문항 파일. 라운드 경계를 넘는 매칭을 검사하려면 AskUserQuestion의 4문항
#: 한계보다 긴 파일이어야 한다.
TEN_QUESTIONS = "".join(
    f"""## Question {n}
질문 {n} 본문입니까?

A) 첫 보기
B) 둘째 보기
X) Other (please describe after [Answer]: tag below)

[Answer]:

"""
    for n in range(1, 11)
)


def _write(ws: Path, rel: str, text: str) -> Path:
    path = ws / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _sdk(*texts: str) -> list[dict]:
    """AskUserQuestion input의 questions 모양(우리가 보는 최소 필드)."""
    return [{"question": t, "options": [{"label": "첫 보기"},
                                        {"label": "둘째 보기"}]} for t in texts]


def test_answers_land_in_the_matching_question(tmp_path):
    """가장 단순한 왕복: 라운드 1의 답이 같은 번호 문항에 들어간다."""
    path = _write(tmp_path, "aiplc-docs/strategy-questions.md", TEN_QUESTIONS)

    updated = record_answers(str(tmp_path), _sdk("질문 1 본문입니까?"), {"1": "B"})

    assert updated == ["aiplc-docs/strategy-questions.md"]
    qf = parse_question_file("strategy-questions.md", path.read_text(encoding="utf-8"))
    assert qf.questions[0].answer == "B"
    # 나머지는 손대지 않는다 — 한 라운드가 파일 전체를 덮어쓰면 안 된다.
    assert [q.answer for q in qf.questions[1:]] == [None] * 9


def test_later_round_numbering_does_not_overwrite_the_first_questions(tmp_path):
    """**이 테스트가 이 기능의 존재 이유다.**

    라운드 2는 SDK 쪽에서 다시 1번부터 번호가 붙는다. 번호로 맞추면 문항 1~4가
    덮이고 5~8은 영구히 빈 칸으로 남는다. 텍스트로 맞추면 5~8에 들어간다.
    """
    path = _write(tmp_path, "aiplc-docs/strategy-questions.md", TEN_QUESTIONS)

    # 라운드 2: 파일의 문항 5~8이지만 SDK 인덱스는 1~4다.
    sdk = _sdk("질문 5 본문입니까?", "질문 6 본문입니까?",
               "질문 7 본문입니까?", "질문 8 본문입니까?")
    updated = record_answers(str(tmp_path), sdk,
                             {"1": "A", "2": "B", "3": "A", "4": "B"})

    assert updated == ["aiplc-docs/strategy-questions.md"]
    qf = parse_question_file("q.md", path.read_text(encoding="utf-8"))
    answers = {q.number: q.answer for q in qf.questions}
    assert answers[5] == "A"
    assert answers[6] == "B"
    assert answers[7] == "A"
    assert answers[8] == "B"
    # 번호로 맞추는 구현이라면 여기가 A/B/A/B로 채워져 있을 것이다.
    assert answers[1] is None and answers[2] is None
    assert answers[3] is None and answers[4] is None


def test_whitespace_and_case_differences_still_match(tmp_path):
    """모델이 파일과 도구에 같은 문장을 쓰지만 공백·줄바꿈이 갈릴 수 있다.

    파서가 본문 여러 줄을 " "로 join하므로(parsers/questions.py:72) 도구 쪽
    줄바꿈이 그대로 오면 문자열이 어긋난다 — 정규화 없이는 매칭이 깨진다.
    """
    md = """## Question 1
주 사용자는
누구입니까?

A) 사내 운영팀
B) 외부 고객
X) Other (please describe after [Answer]: tag below)

[Answer]:
"""
    path = _write(tmp_path, "aiplc-docs/a-questions.md", md)

    updated = record_answers(str(tmp_path),
                             _sdk("주 사용자는   누구입니까?"), {"1": "B"})

    assert updated == ["aiplc-docs/a-questions.md"]
    qf = parse_question_file("a.md", path.read_text(encoding="utf-8"))
    assert qf.questions[0].answer == "B"


def test_unmatched_question_text_touches_nothing(tmp_path):
    """매칭 실패는 **조용한 오염보다 빈 칸이 낫다.**

    엉뚱한 문항에 답이 박히면 그 파일을 읽는 다음 스테이지가 사용자가 하지 않은
    결정을 사실로 취급한다. 답이 없는 것은 사람이 알아볼 수 있지만 틀린 답은
    알아볼 수 없다.
    """
    path = _write(tmp_path, "aiplc-docs/a-questions.md", TEN_QUESTIONS)
    before = path.read_text(encoding="utf-8")

    updated = record_answers(str(tmp_path),
                             _sdk("파일에 없는 질문입니다"), {"1": "B"})

    assert updated == []
    assert path.read_text(encoding="utf-8") == before


def test_only_the_file_holding_the_question_is_rewritten(tmp_path):
    """워크스페이스에는 스테이지마다 질문 파일이 쌓인다. 지난 스테이지 파일을
    건드리면 그때의 기록이 훼손된다."""
    target = _write(tmp_path, "aiplc-docs/discovery/gtm-questions.md", TEN_QUESTIONS)
    other = _write(tmp_path, "aiplc-docs/strategy-questions.md",
                   TEN_QUESTIONS.replace("질문", "다른 질문"))
    other_before = other.read_text(encoding="utf-8")

    updated = record_answers(str(tmp_path), _sdk("질문 3 본문입니까?"), {"1": "A"})

    assert updated == ["aiplc-docs/discovery/gtm-questions.md"]
    assert other.read_text(encoding="utf-8") == other_before
    qf = parse_question_file("g.md", target.read_text(encoding="utf-8"))
    assert {q.number: q.answer for q in qf.questions}[3] == "A"


def test_other_answers_are_recorded_verbatim(tmp_path):
    """"Other"의 값은 `X: 부연` 모양으로 온다(agent/answer_store.py 헤더).

    ai-plc의 Other 규약도 `[Answer]:` 뒤에 설명을 적는 것이므로 그대로 옮긴다.
    """
    path = _write(tmp_path, "aiplc-docs/a-questions.md", TEN_QUESTIONS)

    record_answers(str(tmp_path), _sdk("질문 2 본문입니까?"),
                   {"1": "X: 사내 감사팀이 먼저 본다"})

    qf = parse_question_file("a.md", path.read_text(encoding="utf-8"))
    assert {q.number: q.answer for q in qf.questions}[2] == "X: 사내 감사팀이 먼저 본다"


def test_multi_select_answers_are_recorded_verbatim(tmp_path):
    """복수 선택은 "A,C"로 온다(test_serialize_answers의 계약과 같다)."""
    path = _write(tmp_path, "aiplc-docs/a-questions.md", TEN_QUESTIONS)

    record_answers(str(tmp_path), _sdk("질문 4 본문입니까?"), {"1": "A,B"})

    qf = parse_question_file("a.md", path.read_text(encoding="utf-8"))
    assert {q.number: q.answer for q in qf.questions}[4] == "A,B"


def test_a_broken_file_never_fails_the_turn(tmp_path):
    """되기록은 부수 기록이다 — 실패해도 턴을 죽이지 않는다.

    _save_answers_quietly와 같은 규율이다(claude_driver.py:903). 답변은 이미
    사용자가 제출했고, 파일 기록이 안 됐다고 턴을 죽이면 그 답변이 사라진다.
    """
    _write(tmp_path, "aiplc-docs/broken-questions.md", "질문이 없는 파일\n")

    # 파싱 실패 파일이 섞여 있어도 예외가 새지 않는다.
    assert record_answers(str(tmp_path), _sdk("아무 질문"), {"1": "A"}) == []


def test_no_workspace_directory_is_not_an_error(tmp_path):
    """aiplc-docs/가 아직 없는 초기 턴에도 호출될 수 있다."""
    assert record_answers(str(tmp_path / "nope"), _sdk("질문"), {"1": "A"}) == []


def test_answer_keys_that_do_not_index_a_question_are_skipped(tmp_path):
    """답변 dict의 키는 SDK 라운드 내 1-based 인덱스다. 범위를 벗어난 키는
    무시한다 — claude_driver._on_can_use_tool의 sdk_answers 조립과 같은 방어다."""
    path = _write(tmp_path, "aiplc-docs/a-questions.md", TEN_QUESTIONS)

    updated = record_answers(str(tmp_path), _sdk("질문 1 본문입니까?"),
                             {"1": "A", "9": "B", "notanint": "C"})

    assert updated == ["aiplc-docs/a-questions.md"]
    qf = parse_question_file("a.md", path.read_text(encoding="utf-8"))
    answers = {q.number: q.answer for q in qf.questions}
    assert answers[1] == "A"
    assert all(answers[n] is None for n in range(2, 11))


def test_strands_payload_uses_text_instead_of_question(tmp_path):
    """두 드라이버의 필드명이 다르다 — 한쪽만 지원하면 그쪽만 조용히 빈 칸이 된다.

    SDK AskUserQuestion은 `question`, Strands `ask_questions`의 정규화 페이로드는
    `text`다(questions_payload._normalize_question).
    """
    path = _write(tmp_path, "aiplc-docs/a-questions.md", TEN_QUESTIONS)

    strands = [{"text": "질문 6 본문입니까?", "options": [{"letter": "A"}]}]
    updated = record_answers(str(tmp_path), strands, {"1": "A"})

    assert updated == ["aiplc-docs/a-questions.md"]
    qf = parse_question_file("a.md", path.read_text(encoding="utf-8"))
    assert {q.number: q.answer for q in qf.questions}[6] == "A"


def test_duplicate_question_text_across_rounds_is_idempotent(tmp_path):
    """같은 질문을 두 번 물으면 마지막 답이 남는다(재제출과 같은 의미)."""
    path = _write(tmp_path, "aiplc-docs/a-questions.md", TEN_QUESTIONS)

    record_answers(str(tmp_path), _sdk("질문 1 본문입니까?"), {"1": "A"})
    record_answers(str(tmp_path), _sdk("질문 1 본문입니까?"), {"1": "B"})

    qf = parse_question_file("a.md", path.read_text(encoding="utf-8"))
    assert qf.questions[0].answer == "B"
