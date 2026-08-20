# backend/aipds/survey/builder.py — PROTOTYPE spec -> survey questions.
#
# One agent turn, not the Discovery StrandsDriver: that driver bakes in the
# AIPLC rules system prompt, the workspace tool set and a session manager, none
# of which belong in a stateless "turn this spec into questions" call.
from __future__ import annotations

import json
import logging
import re

from aipds.survey.models import Questionnaire

_log = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

# The validation rule judges a prototype by feature-level signal and pain-point
# mapping (prototype-validation.md Step 6), so the questions must produce that
# evidence -- otherwise the PM gets answers they cannot synthesise.
QUESTIONNAIRE_PROMPT_KO = """\
**문항·제목·선택지·가설을 모두 한국어로 써라.** 아래 명세는 영어 헤딩을 쓰거나
다른 언어로 쓰여 있을 수 있다 — 필요하면 옮겨 쓰고, 문항 자체는 한국어여야 한다.

아래는 프로토타입 명세(PROTOTYPE-*.md)다. 이 **프로토타입을 체험해 본**
사람에게 물을 검증 설문 문항을 만들어라.

응답자가 본 것이 무엇인지가 문항의 전제다. **완성된 제품이 아니라 검증용
프로토타입이다** — 핵심 흐름만 동작하는 데모이고, 데이터는 목(mock)일 수
있고, 일부 기능은 화면만 있을 수 있다. 프로덕션 코드가 아니며 보안·에러
처리·확장성은 의도적으로 만들지 않았다(prototype-validation.md Step 3).

그러므로 다음은 묻지 않는다 — 프로토타입에서는 답할 수 없고, 답을 받아도
설계상 그렇게 만든 것에 대한 감점이라 어떤 판단에도 쓸 수 없다:
- 성능·응답 속도·안정성(오류·다운타임)
- 보안·권한·개인정보 처리
- 실제 데이터의 정확성(목 데이터를 보고 판단할 수 없다)
- 도입 시점·가격·계약 같은 구매 결정
- 프로덕션 운영·유지보수·다른 시스템과의 연동 완성도

대신 프로토타입이 실제로 답을 줄 수 있는 것을 묻는다: 문제 인식이 맞았는지,
제안한 접근이 그 문제를 풀 방향인지, 흐름이 이해되는지, 무엇이 빠졌는지.
"이 방식이 실제 업무에 도입된다면"처럼 **가정형**으로 물어 응답자가 데모의
완성도가 아니라 접근 자체를 평가하게 한다.

요구사항:
- 문항 6~10개.
- 명세의 검증 가설·성공 기준이 참인지 판단할 근거를 얻는 문항을 포함한다.
- 각 주요 기능이 사용자의 문제를 **풀 방향인지** 묻는 문항을 포함한다.
- 개선점·누락된 요구를 드러내는 자유 응답 문항을 최소 1개 포함한다.
- 특정 기능을 묻는 choice 문항에는 **"사용하지 않았다/해당 없음"** 같은
  선택지를 넣는다. 프로토타입에서 일부 기능에 도달하지 못하는 것은 정상이고
  (룰의 Feature Validation 표에 "Not tested — Users did not reach this
  feature" 행이 있다), 그 선택지가 없으면 응답자가 써 보지 않은 기능을
  추측으로 평가해 집계가 실제 신호와 잡음을 구별할 수 없게 된다.
- 유도 질문(원하는 답을 암시하는 질문)을 쓰지 않는다.

문항 타입은 정확히 다음 3종만 사용한다:
- "scale": 1~5 척도. options를 넣지 않는다.
- "choice": 단일 선택. options에 2개 이상의 선택지를 넣는다.
- "text": 자유 응답. options를 넣지 않는다.

출력은 아래 형태의 JSON **하나만** 출력한다(설명·머리말·코드펜스 금지):
{{"title": "...", "hypothesis": "...", "questions": [
  {{"id": "q1", "text": "...", "type": "scale", "required": true}},
  {{"id": "q2", "text": "...", "type": "choice", "options": ["...", "..."], "required": true}},
  {{"id": "q3", "text": "...", "type": "text", "required": false}}
]}}

명세:
---
{md}
---
"""


# 영어 판. 한국어 판과 **같은 제약을 같은 순서로** 담는다 — 조립하지 않고 두
# 벌을 유지하는 이유는 proto/prompts.py와 같다(제약이 하나 빠지면 그 언어의
# 설문만 조용히 나빠진다). test_survey_builder가 두 벌의 대조를 지킨다.
#
# **첫 줄의 언어 지시가 두 판 모두에 있어야 한다.** 프롬프트가 자기 산문의
# 언어로 출력 언어를 암시하는 것만으로는 부족하다 — 실측(2026-08-05): 영어
# 프롬프트에 한국어 명세를 실었더니 문항이 전부 한국어로 나왔다. `{md}`로 실린
# 명세가 더 가깝고 구체적인 신호라 모델이 그것을 따라갔고, ko 프로젝트에서는
# 명세와 출력이 우연히 일치해 결함이 보이지 않았다.
QUESTIONNAIRE_PROMPT_EN = """\
**Write every question, title, option, and hypothesis in English.** The spec below
may be written in another language — translate as needed; the questions themselves
must be in English.

Below is a prototype spec (PROTOTYPE-*.md). Write validation survey questions to
ask someone who **has tried this prototype**.

What the respondent saw is the premise of every question. **This is a validation
prototype, not a finished product** — a demo where only the core flow works, the
data may be mocked, and some features may be screens only. It is not production
code; security, error handling, and scalability were deliberately left out
(prototype-validation.md Step 3).

So do not ask about any of the following — a prototype cannot answer them, and an
answer would only penalize what was built that way on purpose:
- performance, response time, stability (errors, downtime)
- security, permissions, handling of personal data
- accuracy of real data (nobody can judge that from mock data)
- purchase decisions such as timing, pricing, or contracts
- production operations, maintenance, or completeness of integrations

Ask instead about what the prototype really can answer: whether the problem was
identified correctly, whether the proposed approach is a direction that solves it,
whether the flow is understandable, and what is missing. Phrase questions
**hypothetically** ("if this approach were adopted in your actual work…") so the
respondent evaluates the approach rather than the polish of the demo.

Requirements:
- 6 to 10 questions.
- Include questions that produce evidence for judging whether the spec's
  validation hypothesis and success criteria hold.
- Include a question asking whether each major feature is a **direction that
  solves** the user's problem.
- Include at least one free-response question that surfaces improvements and
  missing needs.
- For choice questions about a specific feature, include an option like **"did
  not use it / not applicable"**. Not reaching some features in a prototype is
  normal (the rule's Feature Validation table has a "Not tested — Users did not
  reach this feature" row), and without that option respondents guess about
  features they never tried, which leaves the aggregate unable to tell signal
  from noise.
- Do not write leading questions (questions that hint at the answer you want).

Use exactly these three question types:
- "scale": a 1-5 scale. Do not include options.
- "choice": single select. Include two or more options.
- "text": free response. Do not include options.

Output **only** one JSON object in the shape below (no explanation, no preamble,
no code fence):
{{"title": "...", "hypothesis": "...", "questions": [
  {{"id": "q1", "text": "...", "type": "scale", "required": true}},
  {{"id": "q2", "text": "...", "type": "choice", "options": ["...", "..."], "required": true}},
  {{"id": "q3", "text": "...", "type": "text", "required": false}}
]}}

Spec:
---
{md}
---
"""

_PROMPTS = {"ko": QUESTIONNAIRE_PROMPT_KO, "en": QUESTIONNAIRE_PROMPT_EN}


# ---- Envision 근거 절 (survey/inputs.py가 찾아온 문서) ----
#
# **왜 절마다 지시가 붙어 있는가.** 문서를 싣는 것과 그 문서로 무엇을 하라는
# 지시가 떨어져 있으면 지시가 약해진다 — 이 모듈이 출력 언어에서 이미 겪은
# 실패다(2026-08-05: `{md}`로 실린 명세가 더 가깝고 구체적인 신호라 모델이
# 프롬프트 산문의 언어가 아니라 명세의 언어를 따랐다). 그래서 각 문서 바로
# 뒤에 그 문서를 어떻게 쓸지 붙인다.
#
# **왜 가드가 맨 끝인가.** `pain-point-analysis.md`는 TAM/SAM·지불의향·경쟁
# 구도를 담는데(실측 3/3), 위의 금지 목록은 바로 그 축들을 묻지 말라고 한다.
# 두 신호가 부딪치므로 나중에 오는 쪽이 이겨야 한다.
#
# **왜 조립하는가.** 문서 조합이 셋(페인포인트만/컨텍스트만/둘 다)이라 언어당
# 완성본을 세 벌 두면 여섯 벌이 되고, 그중 하나가 낡는 것을 아무도 못 본다.
# 대신 **조각마다 언어별 완성본 두 벌**을 두고, 문서 유무가 조각의 등장을
# 결정하게 한다 — 문장을 치환으로 만들지 않는다는 규율은 그대로다.

_CTX_PAIN_KO = """
참고 — 페인 포인트 분석 (`envision/pain-point-analysis.md`)
이 프로토타입 명세가 나온 근거 문서다.
---
{text}
---
이 문서를 쓰는 방법:
- 각 문항이 **어느 페인 포인트를 검증하는지** 대응이 서게 만들어라. 룰이
  프로토타입을 기능별 신호와 페인 포인트 대응으로 판정한다
  (prototype-validation.md Step 6).
- 명세에 검증 가설이 명시되어 있지 않으면, 이 문서의 **우선순위 1순위** 페인
  포인트가 해소되는지를 가설로 삼아라.
- 심각도·빈도·현재 우회책은 문항의 **전제**로만 쓴다. 응답자에게 그 표를
  다시 채우게 하지 마라.
"""

_CTX_PAIN_EN = """
Reference — pain point analysis (`envision/pain-point-analysis.md`)
This is the document the prototype spec was derived from.
---
{text}
---
How to use it:
- Make it clear **which pain point each question validates**. The rule judges a
  prototype by feature-level signal and pain-point mapping
  (prototype-validation.md Step 6).
- If the spec states no validation hypothesis, take the **top-ranked** pain
  point in this document and treat "is this resolved?" as the hypothesis.
- Severity, frequency, and current workarounds are **premises** for your
  questions. Do not ask the respondent to fill that table in again.
"""

_CTX_BIZ_KO = """
참고 — 비즈니스 컨텍스트
응답자가 일하는 환경이다.
---
{text}
---
이 문서를 쓰는 방법: 업종·현행 업무 방식에 맞는 용어와 상황으로 문항을 써라.
응답자가 자기 업무로 알아볼 수 있는 문장이어야 한다. 이 문서의 내용을 확인하는
문항(업종이 맞나요, 현재 엑셀을 쓰나요)은 만들지 마라 — 이미 아는 사실이다.
"""

_CTX_BIZ_EN = """
Reference — business context
This is the environment the respondent works in.
---
{text}
---
How to use it: write questions in the vocabulary and situations of that industry
and its current way of working, so the respondent recognizes their own job in
them. Do not write questions that merely confirm this document (is your industry
X, do you use spreadsheets today) — those facts are already known.
"""

_CTX_GUARD_KO = """
위 참고 문서에 대한 제약 — 앞의 금지 목록이 그대로 유효하다.
이 문서들은 **문제와 사용자를 이해하기 위한 것**이고, 그 안에는 설문으로 물어선
안 되는 재료가 섞여 있다. 시장 규모(TAM/SAM)·지불의향·가격·경쟁 구도·도입
시점은 **문항으로 만들지 마라.** 프로토타입을 체험한 사람이 답할 수 있는 것이
아니고, 답을 받아도 판단에 쓸 수 없다. 참고 문서에 그 표가 있다는 것은 물어도
된다는 뜻이 아니다.
"""

_CTX_GUARD_EN = """
Constraint on the reference documents above — the earlier exclusion list still
holds. These documents exist so you **understand the problem and the user**, and
they contain material a survey must not ask about. Do **not** turn market size
(TAM/SAM), willingness to pay, pricing, competitive landscape, or adoption
timing into questions. Someone who tried a prototype cannot answer them, and an
answer could not be used for any judgement. The presence of those tables in a
reference document is not permission to ask about them.
"""

_CTX_PIECES = {
    "ko": (_CTX_PAIN_KO, _CTX_BIZ_KO, _CTX_GUARD_KO),
    "en": (_CTX_PAIN_EN, _CTX_BIZ_EN, _CTX_GUARD_EN),
}


def _context_block(language: str, context) -> str:
    """근거 문서 절. 있는 문서만 등장하고, 하나도 없으면 빈 문자열이다.

    빈 문자열이어야 하는 이유: 없는 문서를 가리키는 지시("아래 페인 포인트
    분석을 보고…")가 남으면 모델은 있지도 않은 절을 찾다가 스펙에서 페인
    포인트를 지어낸다.
    """
    pain_tpl, biz_tpl, guard = _CTX_PIECES[language]
    blocks = []
    if context.pain_points:
        blocks.append(pain_tpl.format(text=context.pain_points))
    if context.business_context:
        blocks.append(biz_tpl.format(text=context.business_context))
    if not blocks:
        return ""
    return "".join(blocks) + guard


def build_prompt(prototype_md: str, language: str = "ko", *,
                 context=None) -> str:
    """설문 문항 생성 프롬프트. 알 수 없는 언어는 한국어로 떨어진다.

    `context`는 `survey/inputs.DiscoveryContext` 또는 None이다. None이면 종전과
    **완전히 같은** 프롬프트가 나온다 — 보조 문서는 보강이고 전제가 아니다.
    """
    lang = language if language in _PROMPTS else "ko"
    prompt = _PROMPTS[lang].format(md=prototype_md)
    if context is None:
        return prompt
    return prompt + _context_block(lang, context)


def _extract_json(reply: str) -> dict:
    fenced = _FENCE_RE.search(reply)
    candidate = fenced.group(1) if fenced else reply
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in reply")
    return json.loads(candidate[start:end + 1])


async def build_questionnaire(prototype_md: str, agent, *, token: str,
                              project_id: str, slug: str, now: str,
                              language: str = "ko",
                              context=None,
                              attempts: int = 2) -> Questionnaire:
    prompt = build_prompt(prototype_md, language, context=context)
    last_error: Exception | None = None
    for attempt in range(attempts):
        reply = await agent(prompt)
        try:
            data = _extract_json(reply)
            return Questionnaire(
                token=token, status="open", slug=slug, project_id=project_id,
                created_at=now, closed_at=None,
                # 문항의 언어를 기록한다 — 공개 응답 페이지가 이 값으로 화면을
                # 그린다. 응답자는 외부인이라 pf_lang 쿠키가 없고, 문항이
                # 영어인데 화면만 한국어인 것은 응답자에게 더 나쁘다.
                language=language,
                title=data["title"], hypothesis=data["hypothesis"],
                questions=data["questions"])
        except Exception as exc:  # noqa: BLE001 — retry on any malformed reply
            last_error = exc
            _log.warning("questionnaire generation attempt %d failed: %s",
                         attempt + 1, exc)
    raise ValueError(f"questionnaire generation failed: {last_error}")
