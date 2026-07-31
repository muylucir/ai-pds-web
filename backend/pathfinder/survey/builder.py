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
QUESTIONNAIRE_PROMPT = """\
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


def build_prompt(prototype_md: str) -> str:
    return QUESTIONNAIRE_PROMPT.format(md=prototype_md)


def _extract_json(reply: str) -> dict:
    fenced = _FENCE_RE.search(reply)
    candidate = fenced.group(1) if fenced else reply
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in reply")
    return json.loads(candidate[start:end + 1])


async def build_questionnaire(prototype_md: str, agent, *, token: str,
                              project_id: str, slug: str, now: str,
                              attempts: int = 2) -> Questionnaire:
    prompt = build_prompt(prototype_md)
    last_error: Exception | None = None
    for attempt in range(attempts):
        reply = await agent(prompt)
        try:
            data = _extract_json(reply)
            return Questionnaire(
                token=token, status="open", slug=slug, project_id=project_id,
                created_at=now, closed_at=None,
                title=data["title"], hypothesis=data["hypothesis"],
                questions=data["questions"])
        except Exception as exc:  # noqa: BLE001 — retry on any malformed reply
            last_error = exc
            _log.warning("questionnaire generation attempt %d failed: %s",
                         attempt + 1, exc)
    raise ValueError(f"questionnaire generation failed: {last_error}")
