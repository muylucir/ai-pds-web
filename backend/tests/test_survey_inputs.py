# backend/tests/test_survey_inputs.py — 설문 생성이 스펙 밖에서 끌어오는
# Envision 산출물의 탐색 규칙.
#
# 파일명이 왜 규칙이 필요한 문제인가(2026-08-20 실측): 상류 룰은 비즈니스
# 컨텍스트를 **자기 파일로 선언하지 않는다**(envision.md Step 0는 5개 영역을
# 수집하라고만 말한다). 그래서 에이전트가 이름을 지어내고, 실제 버킷에서
# 세 가지가 나왔다:
#
#   projects/test1111/.../envision/business-context.md            11055
#   projects/ship/.../envision/business-context-freeform.md        2509
#   files/aiplc-docs/.../envision/business-context.md             11161
#
# 고정 키로는 `ship`을 놓친다. 반대로 접두사만 보면
# `business-context-questions.md`(룰이 선언하는 **질문** 파일)와
# `business-context-followup-questions.md`를 컨텍스트로 착각한다 — 그것들은
# 선택지와 `[Answer]:` 태그가 든 설문지이고, 설문 문항 생성에 넣으면 모델이
# 남의 질문 양식을 베낀다.
#
# 페인포인트는 사정이 다르다. `envision/pain-point-analysis.md`는 룰이 선언하고
# (envision.md:190) 실측 3개 프로젝트에 3/3 존재했으므로 고정 키로 읽는다.
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
    """없으면 None이다 — 예외가 아니다.

    설문 생성은 스펙만으로도 성립한다. 보조 문서가 없다고 502가 되면 Envision을
    건너뛴 프로젝트(또는 아직 Path A.1 스펙만 있는 프로젝트)에서 설문을 아예 만들
    수 없다.
    """
    got = await inputs.gather_context(s3)
    assert got.pain_points is None
    assert got.business_context is None


async def test_finds_business_context_under_its_canonical_name(s3):
    s3.blobs[E + "business-context.md"] = CTX
    got = await inputs.gather_context(s3)
    assert got.business_context == CTX


async def test_finds_business_context_under_an_invented_name(s3):
    """`ship` 프로젝트의 실제 파일명이다. 룰이 이름을 정해주지 않으므로
    `business-context*.md`를 다 후보로 본다."""
    s3.blobs[E + "business-context-freeform.md"] = CTX
    got = await inputs.gather_context(s3)
    assert got.business_context == CTX


@pytest.mark.parametrize("name", [
    "business-context-questions.md",
    "business-context-clarification-questions.md",
    "business-context-followup-questions.md",
])
async def test_question_files_are_not_business_context(s3, name):
    """질문 파일은 컨텍스트가 아니다.

    `business-context-questions.md`는 룰이 선언하는 질문지이고(envision.md:52)
    본문이 선택지 + `[Answer]:` 태그다. 이것을 컨텍스트로 실으면 모델이 남의
    질문 양식을 베껴 설문 문항을 만든다.
    """
    s3.blobs[E + name] = "A) 옵션\nX) Other\n\n[Answer]:"
    got = await inputs.gather_context(s3)
    assert got.business_context is None


async def test_canonical_name_wins_over_a_variant(s3):
    """`test1111`의 실제 상태다 — `business-context.md`(합성본)와
    `business-context-input.md`(원본 입력)가 함께 있다. 합성본이 이긴다."""
    s3.blobs[E + "business-context.md"] = CTX
    s3.blobs[E + "business-context-input.md"] = "원본 입력"
    got = await inputs.gather_context(s3)
    assert got.business_context == CTX
    assert "원본 입력" not in got.business_context


async def test_several_variants_are_all_carried_rather_than_one_picked(s3):
    """정식 이름이 없고 변형이 여러 개면 **전부** 싣는다.

    하나를 고르는 규칙(가장 큰 것, 사전순 첫 것)은 전부 임의적이고, 틀렸을 때
    조용하다 — 떨어진 문서가 로그에도 화면에도 남지 않는다. 상한이 비용을
    막아주므로 버리는 대신 합친다.
    """
    s3.blobs[E + "business-context-freeform.md"] = "자유 서술"
    s3.blobs[E + "business-context-url-extract.md"] = "URL 추출"
    got = await inputs.gather_context(s3)
    assert "자유 서술" in got.business_context
    assert "URL 추출" in got.business_context


async def test_unrelated_envision_documents_are_ignored(s3):
    """Envision 디렉터리에는 다른 산출물이 많다. 접두사만으로 쓸어오면
    PR/FAQ 질문지·모드 선택지가 컨텍스트로 들어간다."""
    s3.blobs[E + "mode-selection-questions.md"] = "모드"
    s3.blobs[E + "prfaq-clarifying-questions.md"] = "PRFAQ 질문"
    s3.blobs[E + "pain-points-from-url.md"] = "URL 페인포인트"
    got = await inputs.gather_context(s3)
    assert got.business_context is None


async def test_oversized_document_is_truncated_and_says_so(s3, caplog):
    """상한을 넘긴 문서는 잘린다 — 그리고 잘렸다고 말한다.

    조용한 절단은 "다 넣었다"로 읽힌다. 실측 최대치가 19.5KB(페인포인트)이므로
    상한에 닿는 것은 병리적인 문서뿐이고, 그때는 로그가 유일한 단서다.
    """
    s3.blobs[E + "pain-point-analysis.md"] = "가" * (inputs.MAX_CHARS + 500)
    with caplog.at_level(logging.WARNING):
        got = await inputs.gather_context(s3)
    assert len(got.pain_points) <= inputs.MAX_CHARS
    assert any("truncat" in r.message.lower() for r in caplog.records), caplog.text


# ---- 이름은 컨텍스트인데 본문이 질문지인 파일 (2026-08-20 실측) ----
#
# `ship`의 `business-context-freeform.md`가 그렇다: 이름에 `question`이 없어
# 이름 필터를 통과하는데, 본문은 `## Question 1~5` + `[Answer]:` 태그의 AIPLC
# 질문지이고 그중 하나만 답변돼 있다. 즉 이름만으로는 못 걸러진다 —
# question_file_answers.py가 이미 기록한 실패와 같다(상류가 자기 명명규칙을
# 어겨 `design-context.md`에 질문을 넣었다: "포함은 내용으로 판단해야 한다").
#
# 그런데 통째로 버리면 `ship`은 비즈니스 컨텍스트를 **완전히 잃는다** — 답변된
# Question 1이 업종·규모·현행 업무 방식을 다 담은 진짜 컨텍스트다. 그래서
# 버리지 않고 **답변만 남긴다.**

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
    """답변은 남고, 선택지와 미응답 태그는 사라진다.

    미응답 `[Answer]:` 태그와 `A)`/`X) Other` 선택지가 프롬프트에 실리면 모델이
    남의 질문 양식을 베껴 설문 문항을 만든다. 미응답 문항은 내용도 없다.
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
    """여러 문단짜리 답변이 첫 줄만 남아선 안 된다.

    실측(2026-08-20): `ship`의 답변은 4개 문단이고, 그중 뒤 3개가 현행 업무
    방식과 병목을 서술한다 — 우리가 정확히 원하는 컨텍스트다. 질문 파서의
    `[Answer]:` 정규식은 **한 줄만** 잡으므로(그 파서의 용도는 AskUserQuestion
    답변 왕복이지 자유 서술이 아니다) 그것으로 답변을 추출하면 1,153자가
    263자가 된다.
    """
    s3.blobs[E + "business-context-freeform.md"] = MULTI_PARAGRAPH_ANSWER
    got = await inputs.gather_context(s3)
    assert "중형 탱커를 건조하는 조선사다" in got.business_context
    assert "설계 변경 정보는 세 곳에 흩어져 있다" in got.business_context
    assert "담당자는 영향 범위 확인에 반나절을 쓴다" in got.business_context
    # 그러면서 질문지 골격은 여전히 사라져 있어야 한다.
    assert "[Answer]:" not in got.business_context
    assert "X) Other" not in got.business_context


async def test_a_questionnaire_with_no_answers_yields_nothing(s3):
    """답변이 하나도 없으면 건질 것이 없다 — None이다."""
    s3.blobs[E + "business-context-freeform.md"] = (
        "## Question 1\n업종은?\n\nA) 제조\nX) Other\n\n[Answer]:\n")
    got = await inputs.gather_context(s3)
    assert got.business_context is None


async def test_prose_that_merely_quotes_the_answer_tag_survives_intact(s3):
    """`[Answer]:` 한 줄이 우연히 있어도 산문은 그대로 남는다.

    문항 헤딩이 없으면 그 문서는 질문지로 **구조화되어 있지 않다.** 그때 답변
    추출을 강행하면 문항 0개 → 결과 없음이 되어, 멀쩡한 컨텍스트 문서가 태그
    한 줄 때문에 통째로 사라진다.
    """
    s3.blobs[E + "business-context.md"] = (
        "# 비즈니스 컨텍스트\n## 1. 업종\n조선업이다.\n"
        "이전 라운드의 응답을 인용한다:\n[Answer]: 조선업\n")
    got = await inputs.gather_context(s3)
    assert "조선업이다" in got.business_context


async def test_the_same_guard_applies_to_the_pain_point_document(s3):
    """페인포인트 문서에도 같은 판정을 쓴다.

    룰은 이 파일을 분석 문서로 선언하지만, 상류가 명명규칙을 어긴 전례가 있으므로
    (`design-context.md`) 이름을 신뢰하지 않는다. 판정을 두 갈래로 두면 "질문
    파일이란 무엇인가"의 답이 두 개가 된다.
    """
    s3.blobs[E + "pain-point-analysis.md"] = SHIP_SHAPED
    got = await inputs.gather_context(s3)
    assert "[Answer]:" not in got.pain_points
    assert "중형 탱커를 건조하는 조선사" in got.pain_points


async def test_a_listing_failure_does_not_fail_the_survey(s3):
    """S3 list가 깨져도 설문 생성은 스펙만으로 계속된다.

    보조 문서는 설문의 전제가 아니라 보강이다. 여기서 예외가 새면
    routes/surveys.py의 502 경로로 떨어져, 있으면 좋았을 문서 때문에 설문을
    아예 못 만든다.
    """
    async def boom(prefix):
        raise RuntimeError("s3 down")

    s3.list = boom
    got = await inputs.gather_context(s3)
    assert got.pain_points is None and got.business_context is None
