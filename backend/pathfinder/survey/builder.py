# backend/pathfinder/survey/builder.py — PROTOTYPE spec -> survey questions.
#
# One agent turn, not the Discovery StrandsDriver: that driver bakes in the
# AIPLC rules system prompt, the workspace tool set and a session manager, none
# of which belong in a stateless "turn this spec into questions" call.
from __future__ import annotations

import json
import logging
import re

from pathfinder.survey.models import Questionnaire

_log = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

# The validation rule judges a prototype by feature-level signal and pain-point
# mapping (prototype-validation.md Step 6), so the questions must produce that
# evidence -- otherwise the PM gets answers they cannot synthesise.
QUESTIONNAIRE_PROMPT_KO = """\
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
QUESTIONNAIRE_PROMPT_EN = """\
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


def build_prompt(prototype_md: str, language: str = "ko") -> str:
    """설문 문항 생성 프롬프트. 알 수 없는 언어는 한국어로 떨어진다."""
    template = _PROMPTS.get(language, QUESTIONNAIRE_PROMPT_KO)
    return template.format(md=prototype_md)


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
                              attempts: int = 2) -> Questionnaire:
    prompt = build_prompt(prototype_md, language)
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
