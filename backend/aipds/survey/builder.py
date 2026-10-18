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
- `personas`에 응답자가 고를 대상 사용자 역할을 2~5개 넣는다. 명세의
  `Target Users`에서 가져오고, 없으면 대상 고객 세그먼트에서 가져온다.
  응답자가 자기 역할로 알아볼 수 있는 짧은 이름으로 쓴다(예: "점포 관리자").
  "기타"는 넣지 마라 — 따로 붙는다. 이 목록으로 AI-PDS가 역할 문항을 직접
  만들어 네 문항 앞에 두고, 집계가 그 답으로 다른 문항을 나눈다. 그러므로
  `questions`에서 응답자의 역할을 묻지 마라 — 두 번 묻게 된다.

문항 타입은 정확히 다음 3종만 사용한다:
- "scale": 1~5 척도. options를 넣지 않는다.
- "choice": 단일 선택. options에 2개 이상의 선택지를 넣는다.
- "text": 자유 응답. options를 넣지 않는다.

출력은 아래 형태의 JSON **하나만** 출력한다(설명·머리말·코드펜스 금지):
{{"title": "...", "hypothesis": "...", "personas": ["...", "..."], "questions": [
  {{"id": "q1", "text": "...", "type": "scale", "required": true}},
  {{"id": "q2", "text": "...", "type": "choice", "options": ["...", "..."], "required": true}},
  {{"id": "q3", "text": "...", "type": "text", "required": false}}
]}}

명세:
---
{md}
---
"""


# English version. It carries **the same constraints in the same order** as the Korean version —
# the reason for keeping two copies rather than assembling them is the same as in
# proto/prompts.py (if one constraint goes missing, only that language's surveys quietly get
# worse). test_survey_builder guards the comparison between the two copies.
#
# **The language instruction on the first line must be in both versions.** Having the prompt
# imply the output language through the language of its own prose is not enough — measured
# (2026-08-05): putting a Korean spec into the English prompt made every question come out in
# Korean. The spec carried in via `{md}` was a closer, more concrete signal, so the model followed
# it, and in ko projects the spec and output happened to match, so the defect was not visible.
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
- Put 2 to 5 target user roles for the respondent to pick from in `personas`.
  Take them from the spec's `Target Users`, or from the target customer segment
  if it has none. Use short names a respondent recognizes as their own role
  (e.g. "store manager"). Do not include "other" — it is added separately.
  AI-PDS itself puts a role question built from this list ahead of your
  questions, and the aggregate splits every other question by its answer. So
  `questions` must not ask for the respondent's role; that would ask it twice.

Use exactly these three question types:
- "scale": a 1-5 scale. Do not include options.
- "choice": single select. Include two or more options.
- "text": free response. Do not include options.

Output **only** one JSON object in the shape below (no explanation, no preamble,
no code fence):
{{"title": "...", "hypothesis": "...", "personas": ["...", "..."], "questions": [
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


# ---- Envision evidence sections (documents found by survey/inputs.py) ----
#
# **Why each section carries its own instruction.** When the document being carried and the
# instruction about what to do with it are far apart, the instruction weakens — a failure this
# module has already experienced with the output language (2026-08-05: the spec carried in via
# `{md}` was a closer, more concrete signal, so the model followed the spec's language rather
# than the language of the prompt prose). So how to use each document is attached right after
# that document.
#
# **Why the guard is at the very end.** `pain-point-analysis.md` contains TAM/SAM, willingness
# to pay, and the competitive landscape (measured 3/3), and the prohibition list above says not
# to ask about exactly those axes. The two signals collide, so the one that comes later must win.
#
# **Why it is assembled.** There are three document combinations (pain points only / context
# only / both), so keeping three complete versions per language would make six, and nobody would
# notice one of them going stale. Instead there are **two complete per-language versions of each
# fragment**, and the presence of a document decides whether its fragment appears — the
# discipline of not building sentences by substitution stays as is.

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
    """The evidence document section. Only the documents that exist appear, and if there are
    none it is an empty string.

    Why it must be an empty string: if an instruction pointing at a nonexistent document ("아래
    페인 포인트 분석을 보고…" — looking at the pain point analysis below…) remains, the model
    searches for a section that does not exist and then makes up pain points from the spec.
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
    """The survey question generation prompt. Unknown languages fall back to Korean.

    `context` is a `survey/inputs.DiscoveryContext` or None. With None, **exactly the same**
    prompt as before comes out — the auxiliary documents are reinforcement, not a premise.
    """
    lang = language if language in _PROMPTS else "ko"
    prompt = _PROMPTS[lang].format(md=prototype_md)
    if context is None:
        return prompt
    return prompt + _context_block(lang, context)


# ---- Persona question ----
#
# **Why it is built here rather than by the model.** The aggregation splits every other question
# by the answer to this one. For that, this question must always exist, it must be identifiable,
# and there must be a choice for respondents who are not the spec's target users. Leaving all
# three to the model loses one of them each time — so only the role names are taken from the
# model, and the question is built here.
#
# **Why "해당 없음/기타" (not applicable / other) must always be there.** The link also goes to
# people who are not target users. Without that choice they pick the closest role and blur that
# persona's signal. If responses pile up on that choice, that in itself is a signal that the
# link went to the wrong place.
#
# **Why the prompt names the subject, "AI-PDS가 직접 만든다" (AI-PDS builds it itself).**
# Measured (2026-10-10, Opus 4.8): when the English version only said "the first question is
# built from this list", 4/4 runs created the role question directly in `questions`, asking for
# the role twice — they read the passive voice as "you build it as the first question". The
# Korean version was 0/4. After naming the subject, both languages are 0/3.

#: Question id. Questions the model builds are q1, q2, …, so there is no overlap.
PERSONA_ID = "persona"

#: Upper limit on the number of role names. The prompt asks for 2-5 — any excess is cut.
MAX_PERSONAS = 6

_PERSONA_QUESTION = {
    "ko": ("다음 중 본인의 역할에 가장 가까운 것은 무엇입니까?", "해당 없음 / 기타"),
    "en": ("Which of these is closest to your role?", "None of these / Other"),
}


def _persona_question(raw, language: str) -> dict | None:
    """Build the first question from the role names the model gave. None if there are no names.

    With None the survey is the same as before this feature — a spec with no target users is no
    reason a survey cannot be built.
    """
    text, other = _PERSONA_QUESTION[language if language in _PERSONA_QUESTION else "ko"]
    names: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        name = item.strip() if isinstance(item, str) else ""
        if name and name != other and name not in names:
            names.append(name)
    if not names:
        return None
    return {"id": PERSONA_ID, "text": text, "type": "choice",
            "options": names[:MAX_PERSONAS] + [other], "required": True,
            "persona": True}


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
            persona = _persona_question(data.get("personas"), language)
            # Questions the model marked as persona in violation of the instructions are not
            # trusted.
            questions = [{**q, "persona": False} if isinstance(q, dict) else q
                         for q in data["questions"]]
            return Questionnaire(
                token=token, status="open", slug=slug, project_id=project_id,
                created_at=now, closed_at=None,
                # Record the question language — the public response page draws the screen
                # with this value. Respondents are outsiders without an aipds_lang cookie, and
                # a screen in Korean while the questions are in English is worse for the
                # respondent.
                language=language,
                title=data["title"], hypothesis=data["hypothesis"],
                questions=([persona] if persona else []) + questions)
        except Exception as exc:  # noqa: BLE001 — retry on any malformed reply
            last_error = exc
            _log.warning("questionnaire generation attempt %d failed: %s",
                         attempt + 1, exc)
    raise ValueError(f"questionnaire generation failed: {last_error}")
