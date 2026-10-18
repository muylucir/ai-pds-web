# backend/tests/test_question_file_answers.py — are answers written back into the question file?
#
# Why this file exists: AI-PDS delivered questions through AskUserQuestion but left the
# question file's `[Answer]:` slots permanently empty. The ai-plc workflow runs on the
# assumption that those slots get filled — aws-aiplc-rule-details/common/question-format-guide.md
# says "Read the question file / Extract answers after [Answer]: tags", and
# session-continuity.md:31-33 instructs the agent to **read** `strategy-questions.md` when a
# session resumes. If the slots are empty, the resumed session loses the user's decisions.
#
# The core of this test is why the write-back matches **by question text, not by number**.
# AskUserQuestion has a hard schema limit of 4 questions x 4 options, so a 10-question file is
# split into three rounds of 4+4+2, and each round's question numbers restart at 1.
# Matching by number would silently corrupt questions 1-4 with round 2's answers.
from __future__ import annotations

from pathlib import Path

from aipds.agent.question_file_answers import record_answers
from aipds.parsers.questions import parse_question_file

#: A 10-question file. To test matching across round boundaries, the file must be longer
#: than AskUserQuestion's 4-question limit.
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
    """The shape of AskUserQuestion input's questions (the minimum fields we look at)."""
    return [{"question": t, "options": [{"label": "첫 보기"},
                                        {"label": "둘째 보기"}]} for t in texts]


def test_answers_land_in_the_matching_question(tmp_path):
    """The simplest round trip: round 1's answers go into the questions with the same numbers."""
    path = _write(tmp_path, "aiplc-docs/strategy-questions.md", TEN_QUESTIONS)

    updated = record_answers(str(tmp_path), _sdk("질문 1 본문입니까?"), {"1": "B"})

    assert updated == ["aiplc-docs/strategy-questions.md"]
    qf = parse_question_file("strategy-questions.md", path.read_text(encoding="utf-8"))
    assert qf.questions[0].answer == "B"
    # The rest is left untouched — one round must not overwrite the whole file.
    assert [q.answer for q in qf.questions[1:]] == [None] * 9


def test_later_round_numbering_does_not_overwrite_the_first_questions(tmp_path):
    """**This test is the reason this feature exists.**

    On the SDK side, round 2 is numbered from 1 again. Matching by number overwrites
    questions 1-4 and leaves 5-8 permanently empty. Matching by text puts them in 5-8.
    """
    path = _write(tmp_path, "aiplc-docs/strategy-questions.md", TEN_QUESTIONS)

    # Round 2: questions 5-8 in the file, but SDK indexes 1-4.
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
    # An implementation that matched by number would have filled these with A/B/A/B.
    assert answers[1] is None and answers[2] is None
    assert answers[3] is None and answers[4] is None


def test_whitespace_and_case_differences_still_match(tmp_path):
    """The model writes the same sentence to the file and the tool, but whitespace and line
    breaks can differ.

    The parser joins multi-line bodies with " " (parsers/questions.py:72), so if the tool
    side keeps its line breaks the strings diverge — without normalization, matching breaks.
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
    """On a match failure, **an empty slot is better than silent corruption.**

    If an answer lands in the wrong question, the next stage that reads the file treats a
    decision the user never made as fact. A person can notice a missing answer, but cannot
    notice a wrong one.
    """
    path = _write(tmp_path, "aiplc-docs/a-questions.md", TEN_QUESTIONS)
    before = path.read_text(encoding="utf-8")

    updated = record_answers(str(tmp_path),
                             _sdk("파일에 없는 질문입니다"), {"1": "B"})

    assert updated == []
    assert path.read_text(encoding="utf-8") == before


def test_only_the_file_holding_the_question_is_rewritten(tmp_path):
    """Question files accumulate in the workspace, one set per stage. Touching a past
    stage's file would damage that stage's record."""
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
    """The value of "Other" arrives as `X: 부연` (`X: elaboration`) (agent/answer_store.py header).

    ai-plc's Other convention is also to write the explanation after `[Answer]:`, so we copy it as is.
    """
    path = _write(tmp_path, "aiplc-docs/a-questions.md", TEN_QUESTIONS)

    record_answers(str(tmp_path), _sdk("질문 2 본문입니까?"),
                   {"1": "X: 사내 감사팀이 먼저 본다"})

    qf = parse_question_file("a.md", path.read_text(encoding="utf-8"))
    assert {q.number: q.answer for q in qf.questions}[2] == "X: 사내 감사팀이 먼저 본다"


def test_multi_select_answers_are_recorded_verbatim(tmp_path):
    """A multi-select arrives as "A,C" (same contract as test_serialize_answers)."""
    path = _write(tmp_path, "aiplc-docs/a-questions.md", TEN_QUESTIONS)

    record_answers(str(tmp_path), _sdk("질문 4 본문입니까?"), {"1": "A,B"})

    qf = parse_question_file("a.md", path.read_text(encoding="utf-8"))
    assert {q.number: q.answer for q in qf.questions}[4] == "A,B"


def test_a_broken_file_never_fails_the_turn(tmp_path):
    """The write-back is a side record — even if it fails, it does not kill the turn.

    Same discipline as _save_answers_quietly (claude_driver.py:903). The user has already
    submitted the answers, and killing the turn because the file write failed would lose them.
    """
    _write(tmp_path, "aiplc-docs/broken-questions.md", "질문이 없는 파일\n")

    # Even if files that fail to parse are mixed in, no exception leaks out.
    assert record_answers(str(tmp_path), _sdk("아무 질문"), {"1": "A"}) == []


def test_no_workspace_directory_is_not_an_error(tmp_path):
    """It can be called on an early turn when aiplc-docs/ does not exist yet."""
    assert record_answers(str(tmp_path / "nope"), _sdk("질문"), {"1": "A"}) == []


def test_answer_keys_that_do_not_index_a_question_are_skipped(tmp_path):
    """The answer dict's keys are 1-based indexes within the SDK round. Out-of-range keys are
    ignored — the same defense as the sdk_answers assembly in claude_driver._on_can_use_tool."""
    path = _write(tmp_path, "aiplc-docs/a-questions.md", TEN_QUESTIONS)

    updated = record_answers(str(tmp_path), _sdk("질문 1 본문입니까?"),
                             {"1": "A", "9": "B", "notanint": "C"})

    assert updated == ["aiplc-docs/a-questions.md"]
    qf = parse_question_file("a.md", path.read_text(encoding="utf-8"))
    answers = {q.number: q.answer for q in qf.questions}
    assert answers[1] == "A"
    assert all(answers[n] is None for n in range(2, 11))


def test_a_normalized_payload_uses_text_instead_of_question(tmp_path):
    """Both field names for the question text are accepted.

    The raw AskUserQuestion input uses `question`, and the payload normalized to the UI
    contract uses `text` (questions_payload._normalize_question — the shape the frontend reads
    and the shape stored in the answer record). The driver passes the raw form, but if the
    normalized shape comes in from the record or the restore path, it must not silently
    become an empty slot.
    """
    path = _write(tmp_path, "aiplc-docs/a-questions.md", TEN_QUESTIONS)

    normalized = [{"text": "질문 6 본문입니까?", "options": [{"letter": "A"}]}]
    updated = record_answers(str(tmp_path), normalized, {"1": "A"})

    assert updated == ["aiplc-docs/a-questions.md"]
    qf = parse_question_file("a.md", path.read_text(encoding="utf-8"))
    assert {q.number: q.answer for q in qf.questions}[6] == "A"


def test_duplicate_question_text_across_rounds_is_idempotent(tmp_path):
    """If the same question is asked twice, the last answer wins (same meaning as a resubmission)."""
    path = _write(tmp_path, "aiplc-docs/a-questions.md", TEN_QUESTIONS)

    record_answers(str(tmp_path), _sdk("질문 1 본문입니까?"), {"1": "A"})
    record_answers(str(tmp_path), _sdk("질문 1 본문입니까?"), {"1": "B"})

    qf = parse_question_file("a.md", path.read_text(encoding="utf-8"))
    assert qf.questions[0].answer == "B"


# ---- Matching that tolerates corrupted Hangul (claude-code#83033) ----
# When the model writes Hangul in tool parameters as `\uXXXX` escapes and mistypes the hex,
# that code point decodes to a "valid but wrong" syllable. The density is 3-5% of syllables
# and it is intermittent, so within the same turn the file (Write) can be clean while only
# the question (AskUserQuestion) is corrupted — measured on ws-a-v3: file `제공하시겠습니까`
# (U+ACA0) vs asked `제공하시겜습니까` (U+AC9C). Checking only exact matches makes the
# write-back fail silently.
#
# Upstream it is officially unresolved (handed to the model team, not recoverable from the
# CLI), and the hex typos are random, so there is no reverse mapping either. So matching
# allows a tolerance.
#
# Threshold rationale (measured on ws-a-v3, 6 rounds, 21 questions):
#   correct pair             0.9677
#   closest wrong answer     0.5806   <- same "정보를 … 제공하시겠습니까?" sentence pattern
#   all other pairs          <= 0.375
#   max within one round     0.32-0.40
# The gap between 0.97 and 0.58 is empty, so 0.85 leaves wide margin on both sides. Even
# with 3 syllables corrupted in 60 characters it is about 0.95, and 1 syllable in 20
# characters is still 0.95.

#: The corruption exactly as measured. The file side has `겠` (U+ACA0), the asked side `겜` (U+AC9C).
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
    """**This test is the reason this layer exists.** The measured pair, as is."""
    path = _write(tmp_path, "aiplc-docs/mode-selection-questions.md",
                  _one_question(_CLEAN))

    updated = record_answers(str(tmp_path), _sdk(_CORRUPT), {"1": "A"})

    assert updated == ["aiplc-docs/mode-selection-questions.md"]
    qf = parse_question_file("m.md", path.read_text(encoding="utf-8"))
    assert qf.questions[0].answer == "A"


def test_a_similar_but_different_question_is_not_matched(tmp_path):
    """The closest wrong candidate in the measurements (0.5806). If this passed, a decision the
    user never made would be recorded on another question — exactly the case the threshold
    must block."""
    path = _write(tmp_path, "aiplc-docs/a-questions.md", _one_question(_CLEAN))
    before = path.read_text(encoding="utf-8")

    other = "비즈니스 컨텍스트 정보를 어떤 방식으로 제공하시겠습니까?"
    assert record_answers(str(tmp_path), _sdk(other), {"1": "A"}) == []
    assert path.read_text(encoding="utf-8") == before


def test_a_close_pair_inside_one_file_is_left_blank(tmp_path):
    """If two candidates are similarly close, **nothing is written.**

    This is why we require a margin over the runner-up. With only a threshold both could
    pass, and picking one would be a coin toss — a person notices an empty slot but not a
    wrong answer.
    """
    md = (_one_question("부품 교체 이력은 어느 기간까지 남아 있습니까?")
          + "\n" + _one_question("부품 교체 이력은 어느 기간까지 남아 있습니까?")
          .replace("## Question 1", "## Question 2"))
    path = _write(tmp_path, "aiplc-docs/a-questions.md", md)
    before = path.read_text(encoding="utf-8")

    # A question almost equidistant from both — there is no basis to decide which one it is.
    asked = "부품 교체 이력은 어느 기간까지 남아 있습니까?!"
    record_answers(str(tmp_path), _sdk(asked), {"1": "B"})

    assert path.read_text(encoding="utf-8") == before


def test_exact_matches_win_before_fuzzy_claims_a_slot(tmp_path):
    """Exact matches must claim their slots first. If fuzzy matching ran first, a corrupted
    question could take another question's slot, leaving that question itself empty."""
    md = _one_question(_CLEAN) + "\n" + _one_question(_CORRUPT).replace(
        "## Question 1", "## Question 2")
    path = _write(tmp_path, "aiplc-docs/a-questions.md", md)

    # Both questions are asked in one round — each must find its own pair by exact match.
    record_answers(str(tmp_path), _sdk(_CORRUPT, _CLEAN), {"1": "A", "2": "B"})

    qf = parse_question_file("a.md", path.read_text(encoding="utf-8"))
    answers = {q.number: q.answer for q in qf.questions}
    assert answers[1] == "B", "Q1은 깨끗한 원본이므로 _CLEAN(2번 답변 B)의 짝이다"
    assert answers[2] == "A", "Q2는 깨진 사본이므로 _CORRUPT(1번 답변 A)의 짝이다"


def test_a_failed_match_is_logged_with_the_best_candidate(tmp_path, caplog):
    """Fills a diagnostic gap.

    On 2026-08-16 the write-back failed silently and the log was empty, which slowed down
    tracing the cause — because a match failure left no record at all. A failure must record
    the best candidate and its score: that number separates "is the threshold too tight" from
    "was the question never there".

    The score decides the level (the two tests below) — here we only check that **a record
    is left** and that it contains the values needed for diagnosis.
    """
    import logging
    _write(tmp_path, "aiplc-docs/a-questions.md", _one_question(_CLEAN))

    with caplog.at_level(logging.DEBUG, logger="aipds.agent"):
        assert record_answers(str(tmp_path),
                              _sdk("전혀 다른 질문입니다"), {"1": "A"}) == []

    assert any("no match" in r.message.lower() for r in caplog.records), \
        [r.message for r in caplog.records]
    # The next person can only pin down the cause if the best candidate and score are there.
    assert any("best" in r.message and "candidate=" in r.message
               for r in caplog.records), [r.message for r in caplog.records]


def test_a_fuzzy_match_is_logged_so_suppression_can_be_measured(tmp_path, caplog):
    """A fuzzy match means **that round's Hangul was corrupted**.
    This log is the only way to tell whether layer 1 (the literal UTF-8 instruction) is working."""
    import logging
    _write(tmp_path, "aiplc-docs/a-questions.md", _one_question(_CLEAN))

    with caplog.at_level(logging.INFO, logger="aipds.agent"):
        record_answers(str(tmp_path), _sdk(_CORRUPT), {"1": "A"})

    assert any("fuzzy" in r.message.lower() for r in caplog.records), \
        [r.message for r in caplog.records]


# ---- Candidate selection: by content, not by name ----
# 2026-08-16 ws-a-v5: `design-context.md` was a complete question file (3 questions,
# 3 `[Answer]:` slots), yet not a single answer was recorded. The candidate glob was
# `*-questions.md` and that name did not match it.
#
# We cannot rely on names because **upstream does not follow its own naming rule**:
# question-format-guide.md prescribes `{phase-name}-questions.md`, but
# prototype-validation.md names Step 2's artifact `design-context.md` while telling the agent
# to use the question format inside it.
#
# Measurements show widening is safe (15 files in ws-a-v5's aiplc-docs): the only addition is
# design-context.md, and all 8 others such as audit.md, discovery-document.md, and
# prototype-spec.md are not matched.

def test_a_question_file_not_named_questions_md_is_still_filled(tmp_path):
    """**This test reproduces that defect.**"""
    path = _write(tmp_path, "aiplc-docs/discovery/prototype/design-context.md",
                  _one_question("이 프로토타입은 어떤 모습이어야 합니까?"))

    updated = record_answers(str(tmp_path),
                             _sdk("이 프로토타입은 어떤 모습이어야 합니까?"),
                             {"1": "A"})

    assert updated == ["aiplc-docs/discovery/prototype/design-context.md"]
    qf = parse_question_file("d.md", path.read_text(encoding="utf-8"))
    assert qf.questions[0].answer == "A"


def test_documents_that_merely_mention_an_answer_tag_are_not_touched(tmp_path):
    """`audit.md` **quotes** questions and `[Answer]: B`. They are not at the start of a line
    and there is no `## Question N` header, so it is not a question file — if the answer
    write-back overwrote the audit record, the workshop record would be damaged."""
    audit = _write(tmp_path, "aiplc-docs/audit.md",
                   "## Envision Step 1\n\n**Q1**: \"질문 1 본문입니까?\"\n"
                   "**Recorded Answer Tag**: `[Answer]: B`\n")
    before = audit.read_text(encoding="utf-8")
    _write(tmp_path, "aiplc-docs/a-questions.md", TEN_QUESTIONS)

    record_answers(str(tmp_path), _sdk("질문 1 본문입니까?"), {"1": "A"})

    assert audit.read_text(encoding="utf-8") == before


def test_a_document_with_no_answer_slot_is_not_even_parsed(tmp_path, caplog):
    """If widening the candidates meant running the parser on every document, each parse
    failure would print a warning + stack trace and bury the log in noise
    (parse_question_file's fallback warning). So we cheaply filter on whether an
    `[Answer]:` slot exists **before parsing**."""
    import logging
    _write(tmp_path, "aiplc-docs/discovery/discovery-document.md",
           "# Discovery Document\n\n## Part 1\n\n산문입니다.\n" * 20)
    _write(tmp_path, "aiplc-docs/a-questions.md", TEN_QUESTIONS)

    with caplog.at_level(logging.WARNING, logger="aipds.parsers.questions"):
        record_answers(str(tmp_path), _sdk("질문 1 본문입니까?"), {"1": "A"})

    assert not [r for r in caplog.records
                if "falling back to raw markdown" in r.message], \
        [r.message for r in caplog.records]


# ---- Failure logs are split into two kinds ----
# Of ws-a-v5's 5 `no match` cases, **3 were not defects** — they were gate/approval
# questions, which have no question file in the first place and are recorded only in
# audit.md. Grouping them under the same warning as real defects (present in the file but
# not found) buries the real ones in noise. The best score separates the two.

def test_a_round_with_no_question_file_is_not_a_warning(tmp_path, caplog):
    """For a gate question, having no file is normal. There is nowhere to record it, so there is no empty slot either."""
    import logging
    _write(tmp_path, "aiplc-docs/a-questions.md", _one_question(_CLEAN))

    with caplog.at_level(logging.DEBUG, logger="aipds.agent"):
        assert record_answers(str(tmp_path),
                              _sdk("regmatrix 제작으로 진행하시겠습니까?"),
                              {"1": "A"}) == []

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], \
        [r.message for r in caplog.records]
    # Still leave a trace — if it vanished silently, the next diagnosis would again start with no log.
    assert caplog.records


def test_a_near_miss_is_a_warning(tmp_path, caplog):
    """The question is in the file but did not clear the threshold. This needs investigating,
    so it must be a warning — it tells whether the threshold is too tight or the Hangul was
    badly corrupted."""
    import logging
    # Measured 0.7692 — a pair below the threshold (0.85) but at or above the near line (0.70).
    _write(tmp_path, "aiplc-docs/a-questions.md",
           _one_question("고객 페인 포인트에 대한 자료를 어떤 경로로 준비하시겠습니까?"))

    with caplog.at_level(logging.DEBUG, logger="aipds.agent"):
        record_answers(str(tmp_path), _sdk(_CLEAN), {"1": "A"})

    assert [r for r in caplog.records if r.levelno >= logging.WARNING], \
        [(r.levelname, r.message) for r in caplog.records]


# ---- Questions with background prose attached are matched too ----
# The file holds background + question together, while only the question sentence goes to
# AskUserQuestion. So comparing `q.text` (~200 chars) with the tool's question (~22 chars)
# collapses the similarity because of the length difference — ws-a-v5's design-context.md Q4
# was lost at 0.3721. We fix it by also comparing against `q.ask` (the last paragraph). It is
# a structural relationship, so it is not a special case.

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
    """**A reproduction of the measured case.** The question sent to the tool was only the last paragraph."""
    path = _write(tmp_path, "aiplc-docs/discovery/prototype/design-context.md",
                  _WITH_BACKGROUND)

    updated = record_answers(str(tmp_path),
                             _sdk("모바일에서 어떤 작업까지 하실 계획입니까?"),
                             {"1": "A"})

    assert updated == ["aiplc-docs/discovery/prototype/design-context.md"]
    qf = parse_question_file("d.md", path.read_text(encoding="utf-8"))
    assert {q.number: q.answer for q in qf.questions}[4] == "A"


def test_background_matching_survives_corrupted_hangul(tmp_path):
    """Exactly as measured: `작업까지` -> `작업짝짜`. Both defects overlapped in one round."""
    path = _write(tmp_path, "aiplc-docs/discovery/prototype/design-context.md",
                  _WITH_BACKGROUND)

    record_answers(str(tmp_path),
                   _sdk("모바일에서 어떤 작업짝짜 하실 계획입니까?"), {"1": "B"})

    qf = parse_question_file("d.md", path.read_text(encoding="utf-8"))
    assert {q.number: q.answer for q in qf.questions}[4] == "B"


def test_the_background_itself_does_not_become_a_match(tmp_path):
    """It must not match by riding on the background prose — the tool never asks a background
    sentence, and a long paragraph produces false positives from coincidental fragment matches."""
    path = _write(tmp_path, "aiplc-docs/discovery/prototype/design-context.md",
                  _WITH_BACKGROUND)
    before = path.read_text(encoding="utf-8")

    assert record_answers(str(tmp_path),
                          _sdk("25행 × 3열 매트릭스는 좁은 화면에 들어갑니까?"),
                          {"1": "A"}) == []
    assert path.read_text(encoding="utf-8") == before
