# backend/pathfinder/survey/inputs.py — 설문 문항 생성이 프로토타입 스펙 밖에서
# 끌어오는 Envision 산출물.
#
# **왜 스펙만으로는 부족한가.** 스펙의 `Problem Statement`·`Business Value`는
# 한두 줄 요약이고, 그 요약을 만든 근거(페인포인트별 심각도·빈도·현재 우회책,
# 우선순위와 그 이유, 업종과 현행 업무 방식)는 Envision 산출물에만 있다. 설문은
# 그 근거를 검증하는 것이므로 요약만 보고 문항을 만들면 무엇을 검증하는지 모르는
# 문항이 나온다.
#
# **왜 `discovery-document.md`는 넣지 않는가(2026-08-20 실측).** 설문을 만드는
# 시점(`aiplc-state.md`의 Current Stage가 Prototype & Validation)에 그 문서는
# `# Part 1: Envision` 하나뿐이었고 — Solution Analysis가 `[x]` 완료로 표시된
# 프로젝트에서도 Part 2가 쓰이지 않았다 — 그 안의 `## 페인 포인트 분석 요약`은
# 아래 `pain-point-analysis.md`를 손실 압축한 재진술이다. 즉 고유 기여는 PR/FAQ
# 산문뿐인데, 그 문서의 내부 FAQ에는 가격 책정·TAM·단위 경제성·수익성 달성
# 시점 문항이 20개 넘게 있다(실측: ship 21개, test1111 24개). 그것들은
# survey/builder.py의 프롬프트가 **묻지 말라고 명시한** 축이다. 얻는 것보다
# 끌어당기는 것이 크다.
#
# **모든 조회는 fail-soft다.** 보조 문서는 설문의 전제가 아니라 보강이다. 여기서
# 예외가 새면 routes/surveys.py의 502로 떨어져, 있으면 좋았을 문서 하나 때문에
# 설문을 아예 만들 수 없게 된다.
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from pathfinder.agent.question_file_answers import looks_like_question_file

_log = logging.getLogger(__name__)

#: Envision 산출물이 사는 디렉터리. 프로젝트 단위이지 프로토타입 단위가 아니다 —
#: 그래서 proto/layout.py가 아니라 이 모듈이 갖는다(그쪽은 프로토타입 산출물
#: 레이아웃의 단독 소유자다).
ENVISION_PREFIX = "aiplc-docs/discovery/envision/"

#: 룰이 선언하는 고정 키(envision.md:190). 실측 3개 프로젝트에 3/3 존재했다.
PAIN_POINTS_KEY = ENVISION_PREFIX + "pain-point-analysis.md"

#: 비즈니스 컨텍스트의 정식 이름 — **룰이 선언하지 않는다.** 에이전트가 이름을
#: 지어내므로 아래 `_business_context_keys`가 접두사로 후보를 넓힌다. 이 이름이
#: 있으면 그것이 합성본이므로 다른 변형을 이긴다.
BUSINESS_CONTEXT_KEY = ENVISION_PREFIX + "business-context.md"

_BUSINESS_CONTEXT_STEM = "business-context"

#: 문서 하나에 허용하는 최대 글자수. 실측 최대치는 19,472바이트(한국어
#: 페인포인트 분석)이므로 이 상한에 닿는 것은 병리적인 문서뿐이다. 상한이 있는
#: 이유는 토큰 비용보다 **한 문서가 프롬프트를 지배하는 것**을 막는 것이다.
MAX_CHARS = 40_000


@dataclass(frozen=True)
class DiscoveryContext:
    """설문 생성에 실을 Envision 근거. 둘 다 없을 수 있다."""

    pain_points: str | None = None
    business_context: str | None = None


def _clip(text: str, key: str) -> str:
    """상한으로 자른다. **자랐으면 말한다** — 조용한 절단은 "다 넣었다"로 읽힌다."""
    if len(text) <= MAX_CHARS:
        return text
    _log.warning("truncated %s to %d chars for the survey prompt (was %d)",
                 key, MAX_CHARS, len(text))
    return text[:MAX_CHARS]


#: 질문지의 선택지 줄. `parsers/questions.py`의 `_OPTION`과 같은 형태다.
_OPTION_LINE = re.compile(r"^([A-F]|X)\)\s")

#: 답변 태그. `^` 앵커는 `looks_like_question_file`의 `_ANSWER_SLOT`과 같은
#: 이유로 필수다 — audit 문서는 이 태그를 문장 안에 **인용**한다.
_ANSWER_LINE = re.compile(r"^\[Answer\]:[ \t]*(.*)$")


def _scrub(text: str) -> tuple[str, int, int]:
    """질문지 골격을 걷어낸 본문, 채워진 답변 수, 전체 답변 슬롯 수.

    **줄 단위로 걷어내고 문단을 재구성하지 않는 것이 요점이다.** 답변은 여러
    문단일 수 있고(실측: `ship`의 Question 1 답변은 4개 문단), 그 뒤 문단들이
    현행 업무 방식과 병목을 서술한다 — 우리가 정확히 원하는 컨텍스트다.
    `parse_question_file`로 답변을 뽑으면 그 문단들이 사라진다: 그 파서의
    `[Answer]:` 정규식은 한 줄만 잡고(용도가 AskUserQuestion 답변 왕복이다),
    남은 문단은 다음 문항의 질문 본문으로 흡수된다. 실측으로 1,153자가 263자가
    됐고 잘린 쪽이 본문이었다.

    걷어내는 것은 둘뿐이다 — 선택지 줄과 `[Answer]:` 태그. 태그는 줄을 지우지
    않고 **접두만** 떼서 뒤에 붙은 답변 본문을 살린다.
    """
    kept: list[str] = []
    slots = filled = 0
    for line in text.splitlines():
        if _OPTION_LINE.match(line):
            continue
        answer = _ANSWER_LINE.match(line)
        if answer is None:
            kept.append(line)
            continue
        slots += 1
        body = answer.group(1).strip()
        if body:
            filled += 1
            kept.append(body)
    return "\n".join(kept), filled, slots


def _distill(key: str, text: str) -> str | None:
    """질문지 형태의 문서에서 골격을 걷어낸다. 산문은 그대로 통과한다.

    **왜 이름으로 판정하지 않는가(2026-08-20 실측).** `ship`의
    `business-context-freeform.md`는 이름에 `question`이 없어 이름 필터를
    통과하는데 본문은 `## Question 1~5` + `[Answer]:` 태그의 AIPLC 질문지였다
    (5문항 중 1개만 응답). 미응답 태그와 `A)`/`X) Other` 선택지가 프롬프트에
    실리면 모델이 남의 질문 양식을 베껴 설문 문항을 만든다.

    **왜 버리지 않고 골격만 걷어내는가.** 통째로 버리면 `ship`은 비즈니스
    컨텍스트를 완전히 잃는다 — 답변된 Question 1이 업종·규모·현행 업무 방식을
    담은 진짜 컨텍스트다.

    **판정은 `looks_like_question_file`이 단독으로 한다.** 그 모듈이 그렇게
    정한 이유가 그대로 여기에도 적용된다: 판정이 두 벌이면 "질문 파일이란
    무엇인가"의 답이 두 개가 되고, 한쪽에는 걸리고 다른 쪽에는 안 걸리는
    문서가 생긴다.

    세 갈래이고 각각 다른 사건이다:

      질문지 아님                    -> 원문 그대로
      질문지 + 답변 하나 이상        -> 골격을 걷어낸 본문
      질문지 + 답변 슬롯 있고 전부 빔 -> None (건질 것이 없다)

    마지막 갈래가 필요한 이유: 답변이 하나도 없는 질문지에 남는 것은 질문
    문장뿐이고, 그것을 "비즈니스 컨텍스트"로 실으면 모델이 남의 질문을 자기
    문항으로 옮긴다.

    `[Answer]:`를 우연히 인용한 산문은 `slots`가 채워진 것으로 세어지므로
    첫 갈래처럼 본문이 살아남는다 — 태그 한 줄 때문에 멀쩡한 문서가 사라지지
    않는다.
    """
    if not looks_like_question_file(key, text):
        return text
    scrubbed, filled, slots = _scrub(text)
    if slots and not filled:
        _log.info("%s is an unanswered question file; nothing to carry into the "
                  "survey prompt", key)
        return None
    _log.info("%s is a question file; scrubbed its scaffolding "
              "(%d of %d answer slots filled)", key, filled, slots)
    return scrubbed


async def _get(s3, key: str) -> str | None:
    try:
        text = await s3.get(key)
    except FileNotFoundError:
        return None
    except Exception:  # noqa: BLE001 — 보강 문서 조회 실패가 설문을 막지 않는다
        _log.exception("could not read %s for the survey prompt", key)
        return None
    if not text.strip():
        return None
    distilled = _distill(key, text)
    if distilled is None or not distilled.strip():
        return None
    return _clip(distilled, key)


def _business_context_keys(keys: list[str]) -> list[str]:
    """`business-context*.md` 중 질문지를 뺀 후보. 정식 이름이 있으면 그것만.

    `question`이 든 이름을 빼는 것이 요점이다. `business-context-questions.md`는
    룰이 선언하는 **질문지**이고(envision.md:52) 본문이 선택지와 `[Answer]:`
    태그다 — 컨텍스트로 실으면 모델이 남의 질문 양식을 베낀다. 실측 버킷에는
    `-clarification-questions.md`·`-followup-questions.md`도 있었다.
    """
    candidates = []
    for key in keys:
        name = key[len(ENVISION_PREFIX):]
        if "/" in name or not name.endswith(".md"):
            continue
        if not name.startswith(_BUSINESS_CONTEXT_STEM):
            continue
        if "question" in name:
            continue
        candidates.append(key)
    if BUSINESS_CONTEXT_KEY in candidates:
        # 합성본이 원본 입력(`-input.md`)을 이긴다 — test1111의 실제 상태다.
        return [BUSINESS_CONTEXT_KEY]
    return sorted(candidates)


async def _business_context(s3) -> str | None:
    try:
        keys = await s3.list(ENVISION_PREFIX)
    except Exception:  # noqa: BLE001 — 위와 같은 이유로 강등한다
        _log.exception("could not list %s for the survey prompt", ENVISION_PREFIX)
        return None

    found = []
    for key in _business_context_keys(keys):
        text = await _get(s3, key)
        if text:
            found.append((key, text))
    if not found:
        return None
    if len(found) == 1:
        return found[0][1]
    # 변형이 여럿일 때만 출처를 붙인다. 원본 입력과 합성본이 함께 잡힐 수 있어서,
    # 모델이 같은 사실의 두 판본을 별개 사실로 읽지 않게 하려는 것이다. 하나뿐일
    # 때 붙이면 프롬프트에 의미 없는 S3 키가 새는 것뿐이다 — 절의 제목은
    # survey/builder.py의 프롬프트가 이미 붙인다.
    joined = "\n\n".join(f"[{key}]\n{text}" for key, text in found)
    return _clip(joined, ENVISION_PREFIX + "business-context*.md")


async def gather_context(s3) -> DiscoveryContext:
    """이 프로젝트의 Envision 근거를 모은다. 없는 것은 None으로 남는다."""
    return DiscoveryContext(
        pain_points=await _get(s3, PAIN_POINTS_KEY),
        business_context=await _business_context(s3),
    )
