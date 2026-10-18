import json
import pytest
from aipds.survey.builder import build_prompt, build_questionnaire
from aipds.survey.inputs import DiscoveryContext

#: Stand-in share token. A module constant rather than a literal at each call
#: site because a string literal passed as `token=` is what secret scanners
#: report as a hardcoded credential (bandit B105/B106) -- there is nothing to
#: fix in a fixture, so give them nothing to match.
TOK = "tok"

MD = """# PROTOTYPE-demo
## Use Case Overview
### Success Criteria
- NOTAM 판독 시간 50% 단축
## Tools
### Tool 1: summarize
"""

VALID = {
    "title": "NOTAM 프로토타입 검증",
    "hypothesis": "판독 시간을 절반으로 줄인다",
    "questions": [
        {"id": "q1", "text": "요약이 정확했나요?", "type": "scale", "required": True},
        {"id": "q2", "text": "가장 유용한 기능은?", "type": "choice",
         "options": ["요약", "검색"], "required": True},
        {"id": "q3", "text": "개선점", "type": "text", "required": False},
    ],
}


class FakeAgent:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    async def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0)


def test_prompt_embeds_md_and_type_constraint():
    p = build_prompt(MD)
    assert "NOTAM 판독 시간 50% 단축" in p     # full md is handed to the model
    assert "scale" in p and "choice" in p and "text" in p
    assert "JSON" in p


def test_prompt_frames_the_subject_as_a_prototype_not_a_product():
    """Questions must rest on the premise that what the respondent saw is a 'demo'.

    If the prompt only says it is asking "end users who actually used it", the
    model writes questions for someone who used a finished product -- speed,
    stability, data accuracy, things a prototype cannot answer. The rules
    define that stage explicitly that way: "This is a rapid prototype for
    validation — NOT production code", "Deprioritize: Security hardening,
    error handling edge cases, scalability" (prototype-validation.md Step 3).
    If someone looks at mock data and answers "was the data accurate", that
    number is noise, not evidence for the hypothesis.
    """
    p = build_prompt(MD)
    assert "프로토타입" in p
    # It must say this is not a finished product, and therefore what not to ask.
    assert "완성" in p or "제품" in p or "프로덕션" in p


def test_prompt_excludes_what_a_prototype_cannot_answer():
    """It must explicitly say not to ask about performance, stability, security, or real-data accuracy.

    These are axes the rules deliberately excluded at the build stage. Asking
    about them in the survey gives the prototype low scores, but those scores
    penalise something that was built that way by design, so they cannot
    inform any decision.
    """
    p = build_prompt(MD)
    for axis in ("성능", "보안", "안정"):
        assert axis in p, f"{axis}을(를) 묻지 말라는 지시가 없다"


def test_prompt_allows_for_features_the_respondent_never_reached():
    """There must be a way to express a feature the respondent never reached.

    The rules' Feature Validation table has a "Not tested — Users did not reach
    this feature" row (Step 6). In other words, not reaching some features is
    normal for a prototype. Without that option, respondents rate features they
    never used by guessing, and the aggregation cannot tell that apart from a
    real signal.
    """
    p = build_prompt(MD)
    assert "사용하지 않" in p or "해당 없" in p or "도달" in p


async def test_builds_questionnaire_from_valid_json():
    agent = FakeAgent(json.dumps(VALID, ensure_ascii=False))
    qn = await build_questionnaire(MD, agent, token=TOK, project_id="p1",
                                   slug="demo", now="2026-07-25T00:00:00Z")
    assert qn.token == TOK and qn.project_id == "p1" and qn.slug == "demo"
    assert qn.status == "open" and qn.closed_at is None
    assert [q.id for q in qn.questions] == ["q1", "q2", "q3"]
    assert len(agent.prompts) == 1


async def test_tolerates_fenced_json():
    # Models routinely wrap JSON in ```json fences despite instructions.
    agent = FakeAgent("```json\n" + json.dumps(VALID) + "\n```")
    qn = await build_questionnaire(MD, agent, token=TOK, project_id="p",
                                   slug="s", now="n")
    assert len(qn.questions) == 3


async def test_retries_once_on_unparseable_reply():
    agent = FakeAgent("설문을 만들었습니다!", json.dumps(VALID))
    qn = await build_questionnaire(MD, agent, token=TOK, project_id="p",
                                   slug="s", now="n")
    assert len(qn.questions) == 3
    assert len(agent.prompts) == 2


async def test_retries_once_on_schema_violation():
    bad = {**VALID, "questions": [
        {"id": "q1", "text": "t", "type": "choice", "options": []}]}
    agent = FakeAgent(json.dumps(bad), json.dumps(VALID))
    qn = await build_questionnaire(MD, agent, token=TOK, project_id="p",
                                   slug="s", now="n")
    assert len(qn.questions) == 3


async def test_raises_after_exhausting_attempts():
    agent = FakeAgent("nope", "still nope")
    with pytest.raises(ValueError):
        await build_questionnaire(MD, agent, token=TOK, project_id="p",
                                  slug="s", now="n")
    assert len(agent.prompts) == 2


def test_prompt_is_korean_by_default():
    p = build_prompt("# PROTOTYPE-demo")
    assert any("가" <= c <= "힣" for c in p)


def test_prompt_is_english_for_an_english_project():
    p = build_prompt("# PROTOTYPE-demo", language="en")
    # The prototype spec is embedded in the prompt, so exclude the spec's text.
    body = p.replace("# PROTOTYPE-demo", "")
    assert not any("가" <= c <= "힣" for c in body), body[:400]


@pytest.mark.parametrize("language", ["ko", "en"])
def test_prompt_keeps_every_requirement(language):
    """Both languages must carry the same constraints. If any one is missing, that
    language's survey either asks what a prototype cannot answer (performance,
    security) or leaves the aggregation unable to tell signal from noise (the
    not-applicable option)."""
    p = build_prompt("# spec", language=language)
    assert "scale" in p and "choice" in p and "text" in p     # the 3 question types
    assert "JSON" in p
    assert "hypothesis" in p and "questions" in p             # output schema
    # The "did not use / not applicable" option constraint. Without it,
    # respondents rate features they never used by guessing, and the
    # aggregation cannot tell signal from noise.
    assert ("해당 없음" in p or "not applicable" in p), p[:600]
    # The ban list: do not ask what a prototype cannot answer.
    assert ("보안" in p or "security" in p), p[:600]
    # The instruction to ask hypothetically -- so respondents judge the approach,
    # not the demo's polish.
    assert ("가정형" in p or "hypothetical" in p), p[:600]


def test_an_unknown_language_falls_back_to_korean():
    assert build_prompt("# spec", language="klingon") == build_prompt("# spec")


# A spec may be written in a different language from the project's. A real
# PROTOTYPE-*.md is a mixed document: project-language prose inside the upstream
# rules' **English headings** (## Use Case Overview, etc.) (the template in
# prototype-md-format.md).
SPEC_IN_KOREAN = """# PROTOTYPE-todo.md
## Use Case Overview
### Problem Statement
팀원들이 할 일을 이메일로 주고받아 진행 상황을 알 수 없다.
## Key Features
1. 할 일 등록 - 제목과 담당자를 입력한다
"""


@pytest.mark.parametrize("language", ["ko", "en"])
def test_prompt_states_the_output_language_explicitly(language):
    """The prompt must **state** the output language -- implying it through the
    language of its own prose is not enough.

    Observed defect (2026-08-05): with language="en", every question came out
    in Korean. Nothing in the prompt said which language to write in, and the
    spec embedded via `{md}` was the closer, more concrete signal, so the model
    followed the spec's language. A/B confirmed with real Bedrock calls: a
    prompt differing only by one prepended directive line came out in English.

    Same shape as the failure recorded in discovery-config/CLAUDE.md -- the
    instruction closest in context wins. So the language is stated explicitly,
    not implied.
    """
    p = build_prompt(SPEC_IN_KOREAN, language=language)
    target = "한국어" if language == "ko" else "English"
    directive = [ln for ln in p.splitlines() if target in ln]
    assert directive, f"출력 언어({target}) 지시가 프롬프트에 없다:\n{p[:500]}"


def test_english_prompt_survives_a_korean_spec():
    """In an English prompt, Korean must appear **only inside the spec**.

    If Hangul remains in what is left after removing the spec (= the
    instructions), the prompt is speaking two languages, and which one the
    model follows is unpredictable.
    """
    p = build_prompt(SPEC_IN_KOREAN, language="en")
    body = p.replace(SPEC_IN_KOREAN, "")
    assert not any("가" <= c <= "힣" for c in body), body[:400]


# ---- Carrying the Envision evidence along (DiscoveryContext in survey/inputs.py) ----
#
# Why the spec alone is not enough: the spec's `Problem Statement` and
# `Business Value` are one- or two-line summaries, and the evidence behind them
# (per-pain-point severity, frequency, and current workarounds; priorities and
# their reasons; industry and current way of working) exists only in the
# Envision artifacts. The survey validates that evidence, so questions written
# from the summary alone do not know what they are validating.

PAIN_POINTS = """# 범주화 페인 포인트 분석
## 1. 목표 고객
심사청구팀 실무자
## 2. 페인 포인트 범주
| 페인 포인트 | 심각도 | 빈도 |
| 조정 사유가 진료과에 전달되지 않는다 | High | Daily |
## 3. 우선순위 순위
1. 재발 방지 피드백 부재
## 5. 시장 평가
- TAM: 연 120억
## 6. 경쟁 환경
| 대체재 | 강점 |
"""

BUSINESS_CONTEXT = """# 비즈니스 컨텍스트
## 1. 업종 및 비즈니스 도메인
400병상 2차 종합병원의 건강보험 심사청구 업무
## 5. 현재 고객 문제를 해결하는 방식
엑셀과 메일로 수작업 취합한다
"""

FULL = DiscoveryContext(pain_points=PAIN_POINTS,
                        business_context=BUSINESS_CONTEXT)


def test_context_documents_reach_the_prompt():
    p = build_prompt(MD, context=FULL)
    assert "조정 사유가 진료과에 전달되지 않는다" in p
    assert "엑셀과 메일로 수작업 취합한다" in p


def test_absent_context_adds_nothing():
    """Without the supporting documents, the prompt must be the same as before.

    If an instruction pointing at an absent document ("아래 페인포인트 분석을
    보고…", "see the pain-point analysis below…") remains, the model looks for a
    section that does not exist and then invents pain points from the spec.
    """
    assert build_prompt(MD, context=DiscoveryContext()) == build_prompt(MD)
    assert build_prompt(MD, context=None) == build_prompt(MD)


def test_one_absent_document_does_not_drag_in_the_other_label():
    """When only one is present, the other one's section does not appear."""
    only_pain = build_prompt(MD, context=DiscoveryContext(pain_points=PAIN_POINTS))
    assert "조정 사유가 진료과에 전달되지 않는다" in only_pain
    assert "엑셀과 메일로 수작업 취합한다" not in only_pain
    assert "비즈니스 컨텍스트" not in only_pain


@pytest.mark.parametrize("language,market,competition", [
    ("ko", "시장", "경쟁"),
    ("en", "market", "competit"),
])
def test_context_carries_a_guard_against_the_banned_axes(language, market,
                                                         competition):
    """When evidence documents are embedded, the **ban list must be restated**.

    `pain-point-analysis.md` contains TAM/SAM, willingness to pay, and the
    competitive landscape (measured: present as sections 5 and 6 in 3/3
    projects). But this prompt already says explicitly not to ask about
    pricing, contracts, or purchase decisions. Adding that material without a
    guard makes the two signals fight, and the closer, more concrete one (the
    table just embedded) wins -- the same shape as the failure this module
    already hit with the output language (2026-08-05).
    """
    p = build_prompt(MD, context=FULL, language=language)
    tail = p.split(PAIN_POINTS)[-1] + p.split(BUSINESS_CONTEXT)[-1]
    assert market in tail.lower(), tail[:800]
    assert competition in tail.lower(), tail[:800]


@pytest.mark.parametrize("language,needle", [
    ("ko", "페인"),
    ("en", "pain point"),
])
def test_pain_point_section_asks_for_question_to_pain_point_mapping(language,
                                                                   needle):
    """It must say to map each question to the pain point it validates.

    `prototype-validation.md` Step 6 judges a prototype by feature-level signal
    and **pain-point mapping**. Until now that mapping was being guessed from
    the spec summary alone.
    """
    p = build_prompt(MD, context=FULL, language=language)
    assert needle in p.lower()


@pytest.mark.parametrize("language,needle", [
    ("ko", "우선순위"),
    ("en", "top-ranked"),
])
def test_pain_point_section_supplies_a_hypothesis_fallback(language, needle):
    """If the spec has no validation hypothesis, it must say to use the top-ranked pain point as the hypothesis.

    The prompt says to build questions from "the spec's validation hypothesis
    and success criteria", but `## Validation Hypothesis` exists only in Path
    A.1's `prototype-spec.md` (prototype-validation.md:70) and not in Path B's
    `PROTOTYPE-{slug}.md` format (prototype-md-format.md). In Path B that
    instruction has nothing to attach to, so the model invents a hypothesis.
    """
    p = build_prompt(MD, context=FULL, language=language)
    assert needle in p.lower()


def test_english_context_prompt_keeps_its_instructions_in_english():
    """Even when the evidence documents are Korean, the instructions must stay English.

    This module has already hit this failure -- the spec embedded via `{md}`
    was the closer, more concrete signal, so the model followed its language
    (2026-08-05). Embedding two more documents increases that pressure.
    """
    p = build_prompt(MD, context=FULL, language="en")
    body = p.replace(MD, "").replace(PAIN_POINTS, "").replace(BUSINESS_CONTEXT, "")
    assert not any("가" <= c <= "힣" for c in body), body[:600]


async def test_build_questionnaire_passes_the_context_through():
    agent = FakeAgent(json.dumps(VALID, ensure_ascii=False))
    await build_questionnaire(MD, agent, token=TOK, project_id="p", slug="s",
                              now="n", context=FULL)
    assert "조정 사유가 진료과에 전달되지 않는다" in agent.prompts[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["ko", "en"])
async def test_build_questionnaire_records_the_language(language):
    async def agent(prompt):
        return ('{"title": "T", "hypothesis": "H", "questions": '
                '[{"id": "q1", "text": "Q", "type": "text", "required": false}]}')

    qn = await build_questionnaire("# spec", agent, token=TOK,
                                   project_id="p1", slug="demo",
                                   now="2026-08-03T00:00:00+00:00",
                                   language=language)
    # The language must be recorded on the questionnaire so the public response
    # page can render in that language.
    assert qn.language == language


# ---- Persona question ----
#
# The aggregation splits every other question by this question's answer
# (store._aggregate_markdown). So the generator writes the question itself and
# takes only the role names from the model.

@pytest.mark.parametrize("language", ["ko", "en"])
def test_prompt_asks_for_personas_from_the_target_users(language):
    p = build_prompt("# spec", language=language)
    assert '"personas"' in p                  # output schema
    assert "Target Users" in p                # where they come from
    # A separate question asking for the role would make two persona questions.
    assert ("역할을 묻지 마라" in p or "must not ask for the respondent's role" in p)


async def test_personas_become_a_required_first_question_with_an_out():
    reply = {**VALID, "personas": ["점포 관리자", "본사 MD"]}
    qn = await build_questionnaire(MD, FakeAgent(json.dumps(reply, ensure_ascii=False)),
                                   token=TOK, project_id="p", slug="s", now="n")
    first = qn.questions[0]
    assert first.persona and first.type == "choice" and first.required
    # Without an option for respondents who are not target users, they pick the
    # closest role and blur that persona's signal.
    assert first.options == ["점포 관리자", "본사 MD", "해당 없음 / 기타"]
    assert [q.id for q in qn.questions[1:]] == ["q1", "q2", "q3"]


async def test_english_persona_question_is_english():
    reply = {**VALID, "personas": ["store manager"]}
    qn = await build_questionnaire(MD, FakeAgent(json.dumps(reply)), token=TOK,
                                   project_id="p", slug="s", now="n", language="en")
    first = qn.questions[0]
    assert first.options == ["store manager", "None of these / Other"]
    assert not any("가" <= c <= "힣" for c in first.text)


async def test_no_personas_means_the_survey_is_as_before():
    """A spec without target users is no reason the survey cannot be built."""
    for personas in (None, [], ["", "  "], "점포 관리자"):
        reply = dict(VALID) if personas is None else {**VALID, "personas": personas}
        qn = await build_questionnaire(MD, FakeAgent(json.dumps(reply, ensure_ascii=False)),
                                       token=TOK, project_id="p", slug="s", now="n")
        assert [q.id for q in qn.questions] == ["q1", "q2", "q3"], personas
        assert not any(q.persona for q in qn.questions)


async def test_persona_names_are_cleaned():
    reply = {**VALID, "personas": [" 점포 관리자 ", "점포 관리자", "해당 없음 / 기타",
                                   7, "A", "B", "C", "D", "E", "F"]}
    qn = await build_questionnaire(MD, FakeAgent(json.dumps(reply, ensure_ascii=False)),
                                   token=TOK, project_id="p", slug="s", now="n")
    # Filter out duplicates, empty values, and "기타" (Other), cap at the limit
    # (6), then append exactly one "기타".
    assert qn.questions[0].options == ["점포 관리자", "A", "B", "C", "D", "E",
                                       "해당 없음 / 기타"]


async def test_a_model_question_cannot_claim_to_be_the_persona_question():
    reply = {**VALID, "questions": [{**VALID["questions"][1], "persona": True}]}
    qn = await build_questionnaire(MD, FakeAgent(json.dumps(reply, ensure_ascii=False)),
                                   token=TOK, project_id="p", slug="s", now="n")
    assert not any(q.persona for q in qn.questions)
