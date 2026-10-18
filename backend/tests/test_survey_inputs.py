# backend/tests/test_survey_inputs.py — discovery rules for the Envision
# artifacts that survey generation pulls in from outside the spec.
#
# Why file names are a problem that needs rules (measured 2026-08-20): the upstream rules
# **do not declare business context as a file of its own** (envision.md Step 0 only says
# to collect 5 areas). So the agent invents a name, and three variants showed up in the
# real bucket:
#
#   projects/test1111/.../envision/business-context.md            11055
#   projects/ship/.../envision/business-context-freeform.md        2509
#   files/aiplc-docs/.../envision/business-context.md             11161
#
# A fixed key misses `ship`. Conversely, matching only the prefix mistakes
# `business-context-questions.md` (the **question** file the rules declare) and
# `business-context-followup-questions.md` for context — those are questionnaires with
# options and `[Answer]:` tags, and feeding them into survey question generation makes
# the model copy someone else's question format.
#
# Pain points are a different story. `envision/pain-point-analysis.md` is declared by the
# rules (envision.md:190) and existed in 3/3 of the measured projects, so it is read by a fixed key.
from __future__ import annotations

import logging

import pytest

from aipds.survey import inputs
from fakes.in_memory_s3 import FakeS3Store

E = inputs.ENVISION_PREFIX
PAIN = "페인포인트 분석 본문"
CTX = "비즈니스 컨텍스트 본문"


@pytest.fixture()
def s3():
    return FakeS3Store()


async def test_reads_pain_point_analysis_from_its_declared_key(s3):
    s3.blobs[E + "pain-point-analysis.md"] = PAIN
    got = await inputs.gather_context(s3)
    assert got.pain_points == PAIN


async def test_missing_pain_point_analysis_degrades_to_none(s3):
    """If missing, it is None — not an exception.

    Survey generation works from the spec alone. If a missing auxiliary document turned
    into a 502, projects that skipped Envision (or that only have a Path A.1 spec so far)
    could not create surveys at all.
    """
    got = await inputs.gather_context(s3)
    assert got.pain_points is None
    assert got.business_context is None


async def test_finds_business_context_under_its_canonical_name(s3):
    s3.blobs[E + "business-context.md"] = CTX
    got = await inputs.gather_context(s3)
    assert got.business_context == CTX


async def test_finds_business_context_under_an_invented_name(s3):
    """This is the actual file name from the `ship` project. The rules do not fix the name,
    so every `business-context*.md` is a candidate."""
    s3.blobs[E + "business-context-freeform.md"] = CTX
    got = await inputs.gather_context(s3)
    assert got.business_context == CTX


@pytest.mark.parametrize("name", [
    "business-context-questions.md",
    "business-context-clarification-questions.md",
    "business-context-followup-questions.md",
])
async def test_question_files_are_not_business_context(s3, name):
    """Question files are not context.

    `business-context-questions.md` is the questionnaire the rules declare (envision.md:52),
    and its body is options + `[Answer]:` tags. Loading it as context makes the model copy
    someone else's question format when generating survey questions.
    """
    s3.blobs[E + name] = "A) 옵션\nX) Other\n\n[Answer]:"
    got = await inputs.gather_context(s3)
    assert got.business_context is None


async def test_canonical_name_wins_over_a_variant(s3):
    """This is the actual state of `test1111` — `business-context.md` (the synthesized version)
    and `business-context-input.md` (the raw input) exist together. The synthesized one wins."""
    s3.blobs[E + "business-context.md"] = CTX
    s3.blobs[E + "business-context-input.md"] = "원본 입력"
    got = await inputs.gather_context(s3)
    assert got.business_context == CTX
    assert "원본 입력" not in got.business_context


async def test_several_variants_are_all_carried_rather_than_one_picked(s3):
    """With no canonical name and several variants, carry **all** of them.

    Any rule that picks one (the largest, the first alphabetically) is arbitrary, and it is
    silent when wrong — the dropped document leaves no trace in the logs or on screen. The
    size cap bounds the cost, so merge instead of discarding.
    """
    s3.blobs[E + "business-context-freeform.md"] = "자유 서술"
    s3.blobs[E + "business-context-url-extract.md"] = "URL 추출"
    got = await inputs.gather_context(s3)
    assert "자유 서술" in got.business_context
    assert "URL 추출" in got.business_context


async def test_unrelated_envision_documents_are_ignored(s3):
    """The Envision directory holds many other artifacts. Sweeping by prefix alone would pull
    PR/FAQ questionnaires and mode-selection options into the context."""
    s3.blobs[E + "mode-selection-questions.md"] = "모드"
    s3.blobs[E + "prfaq-clarifying-questions.md"] = "PRFAQ 질문"
    s3.blobs[E + "pain-points-from-url.md"] = "URL 페인포인트"
    got = await inputs.gather_context(s3)
    assert got.business_context is None


async def test_oversized_document_is_truncated_and_says_so(s3, caplog):
    """A document over the cap is truncated — and says that it was.

    Silent truncation reads as "everything was included". The measured maximum is 19.5KB
    (pain points), so only pathological documents hit the cap, and then the log is the
    only clue.
    """
    s3.blobs[E + "pain-point-analysis.md"] = "가" * (inputs.MAX_CHARS + 500)
    with caplog.at_level(logging.WARNING):
        got = await inputs.gather_context(s3)
    assert len(got.pain_points) <= inputs.MAX_CHARS
    assert any("truncat" in r.message.lower() for r in caplog.records), caplog.text


# ---- Files named as context whose body is a questionnaire (measured 2026-08-20) ----
#
# `ship`'s `business-context-freeform.md` is one: its name has no `question`, so it passes
# the name filter, but the body is an AIPLC questionnaire of `## Question 1~5` + `[Answer]:`
# tags with only one of them answered. So the name alone cannot filter it out — the same
# failure question_file_answers.py already recorded (upstream broke its own naming rule and
# put questions in `design-context.md`: "inclusion must be judged by content").
#
# But discarding it wholesale makes `ship` **lose its business context entirely** — the
# answered Question 1 is real context covering industry, scale, and current work practices.
# So instead of discarding, **keep only the answers.**

SHIP_SHAPED = """# 비즈니스 컨텍스트 — 자유 서술

## Question 1
현재 사업을 하고 있는 **업종 또는 도메인**은 무엇인가요?
(예: 헬스케어, 이커머스, 물류)

[Answer]: 중형 탱커를 건조하는 조선사이고 동시 진행 호선은 6척이다.

## Question 2
현재 이 도메인에서 비즈니스의 **현황**은 어떤가요?

A) 성장 중
B) 정체
X) Other

[Answer]:
"""


async def test_a_question_shaped_context_file_keeps_only_its_answers(s3):
    """Answers stay; options and unanswered tags go.

    If unanswered `[Answer]:` tags and `A)`/`X) Other` options land in the prompt, the model
    copies someone else's question format to build survey questions. Unanswered questions
    carry no content anyway.
    """
    s3.blobs[E + "business-context-freeform.md"] = SHIP_SHAPED
    got = await inputs.gather_context(s3)
    assert "중형 탱커를 건조하는 조선사" in got.business_context
    assert "[Answer]:" not in got.business_context
    assert "X) Other" not in got.business_context
    assert "성장 중" not in got.business_context


MULTI_PARAGRAPH_ANSWER = """# 비즈니스 컨텍스트 — 자유 서술

## Question 1
업종은 무엇인가요?

[Answer]: 중형 탱커를 건조하는 조선사다.

설계 변경 정보는 세 곳에 흩어져 있다.

담당자는 영향 범위 확인에 반나절을 쓴다.

## Question 2
현황은?

A) 성장 중
X) Other

[Answer]:
"""


async def test_a_multi_paragraph_answer_survives_whole(s3):
    """A multi-paragraph answer must not be cut down to its first line.

    Measured (2026-08-20): `ship`'s answer is 4 paragraphs, and the last 3 describe current
    work practices and bottlenecks — exactly the context we want. The question parser's
    `[Answer]:` regex captures **only one line** (that parser is for AskUserQuestion answer
    round-trips, not free-form prose), so extracting answers with it turns 1,153 characters
    into 263.
    """
    s3.blobs[E + "business-context-freeform.md"] = MULTI_PARAGRAPH_ANSWER
    got = await inputs.gather_context(s3)
    assert "중형 탱커를 건조하는 조선사다" in got.business_context
    assert "설계 변경 정보는 세 곳에 흩어져 있다" in got.business_context
    assert "담당자는 영향 범위 확인에 반나절을 쓴다" in got.business_context
    # Meanwhile the questionnaire skeleton must still be gone.
    assert "[Answer]:" not in got.business_context
    assert "X) Other" not in got.business_context


async def test_a_questionnaire_with_no_answers_yields_nothing(s3):
    """With no answers at all, there is nothing to salvage — None."""
    s3.blobs[E + "business-context-freeform.md"] = (
        "## Question 1\n업종은?\n\nA) 제조\nX) Other\n\n[Answer]:\n")
    got = await inputs.gather_context(s3)
    assert got.business_context is None


async def test_prose_that_merely_quotes_the_answer_tag_survives_intact(s3):
    """Prose stays intact even if it happens to contain one `[Answer]:` line.

    Without question headings, the document is **not structured** as a questionnaire.
    Forcing answer extraction then yields 0 questions → no result, and a perfectly good
    context document disappears entirely because of one tag line.
    """
    s3.blobs[E + "business-context.md"] = (
        "# 비즈니스 컨텍스트\n## 1. 업종\n조선업이다.\n"
        "이전 라운드의 응답을 인용한다:\n[Answer]: 조선업\n")
    got = await inputs.gather_context(s3)
    assert "조선업이다" in got.business_context


async def test_the_same_guard_applies_to_the_pain_point_document(s3):
    """The pain-point document gets the same check.

    The rules declare this file as an analysis document, but upstream has broken its naming
    rule before (`design-context.md`), so the name is not trusted. Two separate checks would
    give two answers to "what is a question file".
    """
    s3.blobs[E + "pain-point-analysis.md"] = SHIP_SHAPED
    got = await inputs.gather_context(s3)
    assert "[Answer]:" not in got.pain_points
    assert "중형 탱커를 건조하는 조선사" in got.pain_points


async def test_a_listing_failure_does_not_fail_the_survey(s3):
    """Even if S3 list fails, survey generation continues from the spec alone.

    Auxiliary documents are an enhancement to the survey, not a precondition. If an
    exception leaked here, it would fall into the 502 path of routes/surveys.py, and a
    nice-to-have document would block survey creation entirely.
    """
    async def boom(prefix):
        raise RuntimeError("s3 down")

    s3.list = boom
    got = await inputs.gather_context(s3)
    assert got.pain_points is None and got.business_context is None
