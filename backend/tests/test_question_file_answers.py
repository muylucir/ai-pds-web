# backend/tests/test_question_file_answers.py — 답변이 질문 파일에 되기록되는가.
#
# 왜 이 파일이 생겼는가: AI-PDS는 질문을 AskUserQuestion으로 전달하면서
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

from aipds.agent.question_file_answers import record_answers
from aipds.parsers.questions import parse_question_file

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


def test_a_normalized_payload_uses_text_instead_of_question(tmp_path):
    """질문 텍스트의 필드명 두 가지를 모두 받는다.

    원본 AskUserQuestion input은 `question`이고, 그것을 UI 계약으로 정규화한
    페이로드는 `text`다(questions_payload._normalize_question — 프론트가 읽는
    모양이자 답변 레코드에 저장되는 모양). 드라이버는 원본을 넘기지만, 레코드나
    복원 경로에서 정규화된 모양이 들어와도 조용히 빈 칸이 되면 안 된다.
    """
    path = _write(tmp_path, "aiplc-docs/a-questions.md", TEN_QUESTIONS)

    normalized = [{"text": "질문 6 본문입니까?", "options": [{"letter": "A"}]}]
    updated = record_answers(str(tmp_path), normalized, {"1": "A"})

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


# ---- 깨진 한글을 견디는 매칭 (claude-code#83033) ----
# 모델이 툴 파라미터의 한글을 `\uXXXX` 이스케이프로 쓰면서 hex를 오타내면, 그
# 코드포인트가 "유효하지만 틀린" 음절로 디코드된다. 밀도는 음절의 3~5%이고
# 간헐적이라, 같은 턴에 파일(Write)은 깨끗한데 질문(AskUserQuestion)만 깨질 수
# 있다 — keumkang-v3 실측: 파일 `제공하시겠습니까`(U+ACA0) vs 물어본
# `제공하시겜습니까`(U+AC9C). 정확 일치만 보면 되기록이 조용히 실패한다.
#
# 상류는 공식 미해결이고(모델 팀 이관, CLI로는 복원 불가) hex 오타가 무작위라
# 역변환도 없다. 그래서 매칭에 허용 범위를 둔다.
#
# 임계값 근거(keumkang-v3 6라운드 21문항 실측):
#   맞는 쌍            0.9677
#   가장 비슷한 오답    0.5806   ← 같은 "정보를 … 제공하시겠습니까?" 문형
#   그 외 모든 쌍       ≤ 0.375
#   한 라운드 내 최대   0.32~0.40
# 0.97과 0.58 사이가 비어 있어 0.85는 양쪽으로 여유가 크다. 60자에 3음절이
# 깨져도 약 0.95, 20자에 1음절이 깨져도 0.95다.

#: 실측된 깨짐 그대로. 파일 쪽은 `겠`(U+ACA0), 물어본 쪽은 `겜`(U+AC9C).
_CLEAN = "고객 페인 포인트에 대한 정보를 어떻게 제공하시겠습니까?"
_CORRUPT = "고객 페인 포인트에 대한 정보를 어떻게 제공하시겜습니까?"


def _one_question(text: str) -> str:
    return f"""## Question 1
{text}

A) 대화형으로 답하겠습니다
B) URL이 있습니다
X) Other (please describe after [Answer]: tag below)

[Answer]:
"""


def test_a_corrupted_syllable_still_matches(tmp_path):
    """**이 테스트가 이 계층의 존재 이유다.** 실측 쌍 그대로."""
    path = _write(tmp_path, "aiplc-docs/mode-selection-questions.md",
                  _one_question(_CLEAN))

    updated = record_answers(str(tmp_path), _sdk(_CORRUPT), {"1": "A"})

    assert updated == ["aiplc-docs/mode-selection-questions.md"]
    qf = parse_question_file("m.md", path.read_text(encoding="utf-8"))
    assert qf.questions[0].answer == "A"


def test_a_similar_but_different_question_is_not_matched(tmp_path):
    """실측에서 가장 비슷한 오답 후보(0.5806)다. 이것이 통과하면 사용자가 하지
    않은 결정이 다른 문항에 기록된다 — 임계값이 막아야 하는 바로 그 경우다."""
    path = _write(tmp_path, "aiplc-docs/a-questions.md", _one_question(_CLEAN))
    before = path.read_text(encoding="utf-8")

    other = "비즈니스 컨텍스트 정보를 어떤 방식으로 제공하시겠습니까?"
    assert record_answers(str(tmp_path), _sdk(other), {"1": "A"}) == []
    assert path.read_text(encoding="utf-8") == before


def test_a_close_pair_inside_one_file_is_left_blank(tmp_path):
    """후보 둘이 비슷하게 가까우면 **아무것도 쓰지 않는다.**

    2등과의 격차를 요구하는 이유다. 임계값만 보면 둘 다 넘을 수 있고, 그때
    고르는 것은 동전 던지기다 — 빈 칸은 사람이 알아보지만 틀린 답은 못 알아본다.
    """
    md = (_one_question("부품 교체 이력은 어느 기간까지 남아 있습니까?")
          + "\n" + _one_question("부품 교체 이력은 어느 기간까지 남아 있습니까?")
          .replace("## Question 1", "## Question 2"))
    path = _write(tmp_path, "aiplc-docs/a-questions.md", md)
    before = path.read_text(encoding="utf-8")

    # 두 문항과 거의 같은 거리에 있는 질문 — 어느 쪽인지 정할 근거가 없다.
    asked = "부품 교체 이력은 어느 기간까지 남아 있습니까?!"
    record_answers(str(tmp_path), _sdk(asked), {"1": "B"})

    assert path.read_text(encoding="utf-8") == before


def test_exact_matches_win_before_fuzzy_claims_a_slot(tmp_path):
    """정확 일치가 먼저 자리를 잡아야 한다. 유사 매칭이 먼저 돌면 깨진 질문이
    다른 문항의 자리를 빼앗고 정작 그 문항은 빈 칸으로 남는다."""
    md = _one_question(_CLEAN) + "\n" + _one_question(_CORRUPT).replace(
        "## Question 1", "## Question 2")
    path = _write(tmp_path, "aiplc-docs/a-questions.md", md)

    # 두 질문을 한 라운드에 묻는다 — 각자 자기 짝을 정확 일치로 찾아야 한다.
    record_answers(str(tmp_path), _sdk(_CORRUPT, _CLEAN), {"1": "A", "2": "B"})

    qf = parse_question_file("a.md", path.read_text(encoding="utf-8"))
    answers = {q.number: q.answer for q in qf.questions}
    assert answers[1] == "B", "Q1은 깨끗한 원본이므로 _CLEAN(2번 답변 B)의 짝이다"
    assert answers[2] == "A", "Q2는 깨진 사본이므로 _CORRUPT(1번 답변 A)의 짝이다"


def test_a_failed_match_is_logged_with_the_best_candidate(tmp_path, caplog):
    """진단 공백을 메운다.

    2026-08-16에 되기록이 조용히 실패했고 로그가 텅 비어서 원인 추적이 늦어졌다 —
    매칭 실패에 아무 기록도 남기지 않았기 때문이다. 실패는 최선 후보와 그 점수를
    남겨야 한다: 그 숫자가 "임계값이 빡빡한가" vs "애초에 없는 질문인가"를 가른다.

    레벨은 점수가 정한다(아래 두 테스트) — 여기서는 **기록이 남는다**는 것과
    진단에 필요한 값이 담긴다는 것만 본다.
    """
    import logging
    _write(tmp_path, "aiplc-docs/a-questions.md", _one_question(_CLEAN))

    with caplog.at_level(logging.DEBUG, logger="aipds.agent"):
        assert record_answers(str(tmp_path),
                              _sdk("전혀 다른 질문입니다"), {"1": "A"}) == []

    assert any("no match" in r.message.lower() for r in caplog.records), \
        [r.message for r in caplog.records]
    # 최선 후보와 점수가 있어야 다음 사람이 원인을 가를 수 있다.
    assert any("best" in r.message and "candidate=" in r.message
               for r in caplog.records), [r.message for r in caplog.records]


def test_a_fuzzy_match_is_logged_so_suppression_can_be_measured(tmp_path, caplog):
    """유사 매칭이 일어났다는 것은 **그 라운드의 한글이 깨졌다**는 뜻이다.
    1층(리터럴 UTF-8 지시)이 듣고 있는지를 이 로그로만 알 수 있다."""
    import logging
    _write(tmp_path, "aiplc-docs/a-questions.md", _one_question(_CLEAN))

    with caplog.at_level(logging.INFO, logger="aipds.agent"):
        record_answers(str(tmp_path), _sdk(_CORRUPT), {"1": "A"})

    assert any("fuzzy" in r.message.lower() for r in caplog.records), \
        [r.message for r in caplog.records]


# ---- 후보 선정: 이름이 아니라 내용으로 ----
# 2026-08-16 keumkang-v5: `design-context.md`가 완전한 질문 파일인데(문항 3개,
# `[Answer]:` 슬롯 3개) 답변이 하나도 기록되지 않았다. 후보 glob이
# `*-questions.md`였고 그 이름에 걸리지 않았기 때문이다.
#
# 이름에 의존할 수 없는 이유는 **상류가 자기 명명 규칙을 스스로 안 지킨다**는 것이다:
# question-format-guide.md는 `{phase-name}-questions.md`를 규정하는데,
# prototype-validation.md는 Step 2의 산출물을 `design-context.md`로 지정하면서 그
# 안에 질문 형식을 쓰라고 한다.
#
# 실측으로 넓혀도 안전하다(keumkang-v5의 aiplc-docs 15개 파일): 추가되는 것은
# design-context.md 하나뿐이고 audit.md·discovery-document.md·prototype-spec.md 등
# 8개는 전부 걸리지 않는다.

def test_a_question_file_not_named_questions_md_is_still_filled(tmp_path):
    """**이 테스트가 그 결함의 재현이다.**"""
    path = _write(tmp_path, "aiplc-docs/discovery/prototype/design-context.md",
                  _one_question("이 프로토타입은 어떤 모습이어야 합니까?"))

    updated = record_answers(str(tmp_path),
                             _sdk("이 프로토타입은 어떤 모습이어야 합니까?"),
                             {"1": "A"})

    assert updated == ["aiplc-docs/discovery/prototype/design-context.md"]
    qf = parse_question_file("d.md", path.read_text(encoding="utf-8"))
    assert qf.questions[0].answer == "A"


def test_documents_that_merely_mention_an_answer_tag_are_not_touched(tmp_path):
    """`audit.md`는 질문과 `[Answer]: B`를 **인용**한다. 줄 맨 앞이 아니고
    `## Question N` 헤더도 없으므로 질문 파일이 아니다 — 감사 기록을 답변
    되기록이 덮으면 워크숍 기록이 훼손된다."""
    audit = _write(tmp_path, "aiplc-docs/audit.md",
                   "## Envision Step 1\n\n**Q1**: \"질문 1 본문입니까?\"\n"
                   "**Recorded Answer Tag**: `[Answer]: B`\n")
    before = audit.read_text(encoding="utf-8")
    _write(tmp_path, "aiplc-docs/a-questions.md", TEN_QUESTIONS)

    record_answers(str(tmp_path), _sdk("질문 1 본문입니까?"), {"1": "A"})

    assert audit.read_text(encoding="utf-8") == before


def test_a_document_with_no_answer_slot_is_not_even_parsed(tmp_path, caplog):
    """후보를 넓히면서 파서를 모든 문서에 돌리면, 파싱 실패마다 경고 + 스택
    트레이스가 찍혀 로그가 잡음으로 덮인다(parse_question_file의 fallback 경고).
    그래서 `[Answer]:` 슬롯 유무를 **파싱 전에** 값싸게 걸러낸다."""
    import logging
    _write(tmp_path, "aiplc-docs/discovery/discovery-document.md",
           "# Discovery Document\n\n## Part 1\n\n산문입니다.\n" * 20)
    _write(tmp_path, "aiplc-docs/a-questions.md", TEN_QUESTIONS)

    with caplog.at_level(logging.WARNING, logger="aipds.parsers.questions"):
        record_answers(str(tmp_path), _sdk("질문 1 본문입니까?"), {"1": "A"})

    assert not [r for r in caplog.records
                if "falling back to raw markdown" in r.message], \
        [r.message for r in caplog.records]


# ---- 실패 로그를 두 종류로 나눈다 ----
# keumkang-v5의 `no match` 5건 중 **3건은 결함이 아니었다** — 게이트/승인 질문이라
# 질문 파일이 애초에 없고 audit.md에만 기록된다. 진짜 결함(파일에 있는데 못 찾음)과
# 같은 경고로 묶으면 잡음에 묻힌다. 최선 점수가 그 둘을 가른다.

def test_a_round_with_no_question_file_is_not_a_warning(tmp_path, caplog):
    """게이트 질문은 파일이 없는 것이 정상이다. 기록할 곳이 없으니 빈 칸도 없다."""
    import logging
    _write(tmp_path, "aiplc-docs/a-questions.md", _one_question(_CLEAN))

    with caplog.at_level(logging.DEBUG, logger="aipds.agent"):
        assert record_answers(str(tmp_path),
                              _sdk("regmatrix 제작으로 진행하시겠습니까?"),
                              {"1": "A"}) == []

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], \
        [r.message for r in caplog.records]
    # 그래도 흔적은 남긴다 — 조용히 사라지면 다음 진단이 또 로그 없이 시작된다.
    assert caplog.records


def test_a_near_miss_is_a_warning(tmp_path, caplog):
    """파일에 그 질문이 있는데 임계값을 못 넘은 경우다. 이건 조사 대상이므로
    경고여야 한다 — 임계값이 빡빡한지, 한글이 심하게 깨졌는지 갈린다."""
    import logging
    # 실측 0.7692 — 임계값(0.85) 미만이면서 근접선(0.70) 이상인 쌍이다.
    _write(tmp_path, "aiplc-docs/a-questions.md",
           _one_question("고객 페인 포인트에 대한 자료를 어떤 경로로 준비하시겠습니까?"))

    with caplog.at_level(logging.DEBUG, logger="aipds.agent"):
        record_answers(str(tmp_path), _sdk(_CLEAN), {"1": "A"})

    assert [r for r in caplog.records if r.levelno >= logging.WARNING], \
        [(r.levelname, r.message) for r in caplog.records]


# ---- 배경 산문이 붙은 문항도 매칭된다 ----
# 파일은 배경 + 질문을 함께 담고 AskUserQuestion에는 질문 문장만 간다. 그래서
# `q.text`(~200자)와 도구의 질문(~22자)을 비교하면 길이 차이로 유사도가 무너진다 —
# keumkang-v5의 design-context.md Q4가 0.3721로 유실됐다. `q.ask`(마지막 문단)와도
# 비교해 해결한다. 구조적 관계이므로 특수 케이스가 아니다.

_WITH_BACKGROUND = """## Question 4 (모호성 해소 — Question 3 답변에 따른 후속)

**작성 시각**: 2026-08-16T22:49:36Z

Question 3에서 D(데스크톱과 모바일 모두)를 선택했다. 이는 모순은 아니지만 해소해야 할
모호성을 남긴다. 25행 × 3열 매트릭스는 좁은 화면에 그대로 들어가지 않으므로, 모바일에서는
격자가 아니라 목록 형태의 별도 레이아웃이 필요하다.

모바일에서 어떤 작업까지 하실 계획입니까?

A) 열람과 근거 확인까지만
B) 데스크톱과 동일한 모든 기능
X) Other (please describe after [Answer]: tag below)

[Answer]:
"""


def test_a_question_with_background_prose_matches_on_its_last_paragraph(tmp_path):
    """**실측 재현이다.** 도구에 간 질문은 마지막 문단뿐이었다."""
    path = _write(tmp_path, "aiplc-docs/discovery/prototype/design-context.md",
                  _WITH_BACKGROUND)

    updated = record_answers(str(tmp_path),
                             _sdk("모바일에서 어떤 작업까지 하실 계획입니까?"),
                             {"1": "A"})

    assert updated == ["aiplc-docs/discovery/prototype/design-context.md"]
    qf = parse_question_file("d.md", path.read_text(encoding="utf-8"))
    assert {q.number: q.answer for q in qf.questions}[4] == "A"


def test_background_matching_survives_corrupted_hangul(tmp_path):
    """실측 그대로: `작업까지` → `작업짝짜`. 두 결함이 한 라운드에 겹쳐 있었다."""
    path = _write(tmp_path, "aiplc-docs/discovery/prototype/design-context.md",
                  _WITH_BACKGROUND)

    record_answers(str(tmp_path),
                   _sdk("모바일에서 어떤 작업짝짜 하실 계획입니까?"), {"1": "B"})

    qf = parse_question_file("d.md", path.read_text(encoding="utf-8"))
    assert {q.number: q.answer for q in qf.questions}[4] == "B"


def test_the_background_itself_does_not_become_a_match(tmp_path):
    """배경 산문에 얹혀 매칭되면 안 된다 — 도구가 배경 문장을 물을 일은 없고,
    긴 문단은 우연한 조각 일치로 오탐을 만든다."""
    path = _write(tmp_path, "aiplc-docs/discovery/prototype/design-context.md",
                  _WITH_BACKGROUND)
    before = path.read_text(encoding="utf-8")

    assert record_answers(str(tmp_path),
                          _sdk("25행 × 3열 매트릭스는 좁은 화면에 들어갑니까?"),
                          {"1": "A"}) == []
    assert path.read_text(encoding="utf-8") == before
