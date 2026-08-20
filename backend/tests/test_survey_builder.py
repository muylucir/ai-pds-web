import json
import pytest
from aipds.survey.builder import build_prompt, build_questionnaire
from aipds.survey.inputs import DiscoveryContext

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
    """문항은 응답자가 본 것이 '데모'라는 전제 위에 서야 한다.

    프롬프트가 "실제로 사용해 본 최종 사용자"에게 묻는다고만 말하면 모델은
    완성 제품을 쓴 사람에게 하는 질문을 만든다 — 속도·안정성·데이터 정확도처럼
    프로토타입에서는 답할 수 없는 것들이다. 룰이 그 단계를 명시적으로 그렇게
    규정한다: "This is a rapid prototype for validation — NOT production code",
    "Deprioritize: Security hardening, error handling edge cases, scalability"
    (prototype-validation.md Step 3). 목 데이터를 보고 "데이터가 정확했나요"를
    답하면 그 수치는 가설에 대한 근거가 아니라 잡음이다.
    """
    p = build_prompt(MD)
    assert "프로토타입" in p
    # 완성품이 아니라는 것과, 그래서 무엇을 묻지 말아야 하는지가 있어야 한다.
    assert "완성" in p or "제품" in p or "프로덕션" in p


def test_prompt_excludes_what_a_prototype_cannot_answer():
    """성능·안정성·보안·실데이터 정확도는 묻지 말라고 명시해야 한다.

    룰이 빌드 단계에서 의도적으로 배제한 축들이다. 그것을 설문에서 물으면
    프로토타입이 낮은 점수를 받는데, 그 점수는 설계상 그렇게 만든 것에 대한
    감점이라 어떤 판단에도 쓸 수 없다.
    """
    p = build_prompt(MD)
    for axis in ("성능", "보안", "안정"):
        assert axis in p, f"{axis}을(를) 묻지 말라는 지시가 없다"


def test_prompt_allows_for_features_the_respondent_never_reached():
    """도달하지 못한 기능을 표현할 길이 있어야 한다.

    룰의 Feature Validation 표에 "Not tested — Users did not reach this
    feature" 행이 있다(Step 6). 즉 일부 기능에 닿지 못하는 것이 프로토타입에서는
    정상이다. 그 선택지가 없으면 응답자는 안 써 본 기능을 추측으로 평가하고,
    집계는 그것을 실제 신호와 구별하지 못한다.
    """
    p = build_prompt(MD)
    assert "사용하지 않" in p or "해당 없" in p or "도달" in p


async def test_builds_questionnaire_from_valid_json():
    agent = FakeAgent(json.dumps(VALID, ensure_ascii=False))
    qn = await build_questionnaire(MD, agent, token="tok", project_id="p1",
                                   slug="demo", now="2026-07-25T00:00:00Z")
    assert qn.token == "tok" and qn.project_id == "p1" and qn.slug == "demo"
    assert qn.status == "open" and qn.closed_at is None
    assert [q.id for q in qn.questions] == ["q1", "q2", "q3"]
    assert len(agent.prompts) == 1


async def test_tolerates_fenced_json():
    # Models routinely wrap JSON in ```json fences despite instructions.
    agent = FakeAgent("```json\n" + json.dumps(VALID) + "\n```")
    qn = await build_questionnaire(MD, agent, token="t", project_id="p",
                                   slug="s", now="n")
    assert len(qn.questions) == 3


async def test_retries_once_on_unparseable_reply():
    agent = FakeAgent("설문을 만들었습니다!", json.dumps(VALID))
    qn = await build_questionnaire(MD, agent, token="t", project_id="p",
                                   slug="s", now="n")
    assert len(qn.questions) == 3
    assert len(agent.prompts) == 2


async def test_retries_once_on_schema_violation():
    bad = {**VALID, "questions": [
        {"id": "q1", "text": "t", "type": "choice", "options": []}]}
    agent = FakeAgent(json.dumps(bad), json.dumps(VALID))
    qn = await build_questionnaire(MD, agent, token="t", project_id="p",
                                   slug="s", now="n")
    assert len(qn.questions) == 3


async def test_raises_after_exhausting_attempts():
    agent = FakeAgent("nope", "still nope")
    with pytest.raises(ValueError):
        await build_questionnaire(MD, agent, token="t", project_id="p",
                                  slug="s", now="n")
    assert len(agent.prompts) == 2


def test_prompt_is_korean_by_default():
    p = build_prompt("# PROTOTYPE-demo")
    assert any("가" <= c <= "힣" for c in p)


def test_prompt_is_english_for_an_english_project():
    p = build_prompt("# PROTOTYPE-demo", language="en")
    # 프로토타입 명세가 프롬프트에 실리므로 명세의 글자는 제외하고 본다.
    body = p.replace("# PROTOTYPE-demo", "")
    assert not any("가" <= c <= "힣" for c in body), body[:400]


@pytest.mark.parametrize("language", ["ko", "en"])
def test_prompt_keeps_every_requirement(language):
    """두 언어가 같은 제약을 담아야 한다. 하나라도 빠지면 그 언어의 설문이
    프로토타입으로 답할 수 없는 것을 묻거나(성능·보안), 집계가 신호와 잡음을
    구별할 수 없게 된다(해당 없음 선택지)."""
    p = build_prompt("# spec", language=language)
    assert "scale" in p and "choice" in p and "text" in p     # 문항 타입 3종
    assert "JSON" in p
    assert "hypothesis" in p and "questions" in p             # 출력 스키마
    # "사용하지 않았다/해당 없음" 선택지 제약. 빠지면 응답자가 써 보지 않은
    # 기능을 추측으로 평가해 집계가 신호와 잡음을 구별할 수 없게 된다.
    assert ("해당 없음" in p or "not applicable" in p), p[:600]
    # 프로토타입으로 답할 수 없는 것을 묻지 말라는 금지 목록.
    assert ("보안" in p or "security" in p), p[:600]
    # 가정형으로 물으라는 지시 — 데모의 완성도가 아니라 접근을 평가하게 한다.
    assert ("가정형" in p or "hypothetical" in p), p[:600]


def test_an_unknown_language_falls_back_to_korean():
    assert build_prompt("# spec", language="klingon") == build_prompt("# spec")


# 명세는 프로젝트 언어와 다른 언어로 쓰여 있을 수 있다. 실제 PROTOTYPE-*.md는
# 상류 룰의 **영어 헤딩**(## Use Case Overview 등) 안에 프로젝트 언어의 산문이
# 담긴 혼합 문서다(prototype-md-format.md의 템플릿).
SPEC_IN_KOREAN = """# PROTOTYPE-todo.md
## Use Case Overview
### Problem Statement
팀원들이 할 일을 이메일로 주고받아 진행 상황을 알 수 없다.
## Key Features
1. 할 일 등록 - 제목과 담당자를 입력한다
"""


@pytest.mark.parametrize("language", ["ko", "en"])
def test_prompt_states_the_output_language_explicitly(language):
    """프롬프트가 출력 언어를 **말해야** 한다 — 자기 산문의 언어로 암시하는
    것으로는 부족하다.

    실측한 결함(2026-08-05): language="en"인데 문항이 전부 한국어로 나왔다.
    프롬프트 어디에도 어느 언어로 쓰라는 지시가 없었고, `{md}`로 실린 명세가
    더 가깝고 구체적인 신호라 모델이 명세의 언어를 따라갔다. Bedrock 실호출로
    A/B 확인: 지시 한 줄을 앞에 붙인 것만 다른 프롬프트는 영어로 나왔다.

    discovery-config/CLAUDE.md가 기록한 그 실패와 같은 모양이다 — 맥락이 가까운
    지시가 이긴다. 그래서 언어를 암시가 아니라 명시로 둔다.
    """
    p = build_prompt(SPEC_IN_KOREAN, language=language)
    target = "한국어" if language == "ko" else "English"
    directive = [ln for ln in p.splitlines() if target in ln]
    assert directive, f"출력 언어({target}) 지시가 프롬프트에 없다:\n{p[:500]}"


def test_english_prompt_survives_a_korean_spec():
    """영어 프롬프트에서 한국어는 **명세 안에만** 있어야 한다.

    명세를 제거한 나머지(=지시문)에 한글이 남아 있으면 그 프롬프트는 두 언어로
    말하는 것이고, 모델이 어느 쪽을 따를지 예측할 수 없다.
    """
    p = build_prompt(SPEC_IN_KOREAN, language="en")
    body = p.replace(SPEC_IN_KOREAN, "")
    assert not any("가" <= c <= "힣" for c in body), body[:400]


# ---- Envision 근거를 함께 싣기 (survey/inputs.py의 DiscoveryContext) ----
#
# 왜 스펙만으로 부족한가: 스펙의 `Problem Statement`·`Business Value`는 한두 줄
# 요약이고, 그 요약을 만든 근거(페인포인트별 심각도·빈도·현재 우회책, 우선순위와
# 그 이유, 업종과 현행 업무 방식)는 Envision 산출물에만 있다. 설문은 그 근거를
# 검증하므로, 요약만 보고 만든 문항은 무엇을 검증하는지 모른다.

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
    """보조 문서가 없으면 프롬프트는 종전과 같아야 한다.

    없는 문서를 가리키는 지시("아래 페인포인트 분석을 보고…")가 남으면 모델은
    있지도 않은 절을 찾다가 스펙에서 페인포인트를 지어낸다.
    """
    assert build_prompt(MD, context=DiscoveryContext()) == build_prompt(MD)
    assert build_prompt(MD, context=None) == build_prompt(MD)


def test_one_absent_document_does_not_drag_in_the_other_label():
    """한쪽만 있을 때 다른 쪽 절은 나오지 않는다."""
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
    """근거 문서를 실으면 **금지 목록을 다시 못 박아야** 한다.

    `pain-point-analysis.md`는 TAM/SAM·지불의향·경쟁 구도를 담는다(실측: 3개
    프로젝트 3/3에 5·6절로 존재). 그런데 이 프롬프트는 가격·계약·구매 결정을
    묻지 말라고 이미 명시한다. 가드 없이 그 재료만 넣으면 두 신호가 서로 싸우고,
    더 가깝고 구체적인 쪽(방금 실린 표)이 이긴다 — 이 모듈이 출력 언어에서 이미
    겪은 실패와 같은 모양이다(2026-08-05).
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
    """문항이 어느 페인포인트를 검증하는지 대응시키라고 말해야 한다.

    `prototype-validation.md` Step 6이 프로토타입을 feature-level signal과
    **pain-point mapping**으로 판정한다. 지금은 스펙 요약만 보고 그 대응을
    추측하고 있었다.
    """
    p = build_prompt(MD, context=FULL, language=language)
    assert needle in p.lower()


@pytest.mark.parametrize("language,needle", [
    ("ko", "우선순위"),
    ("en", "top-ranked"),
])
def test_pain_point_section_supplies_a_hypothesis_fallback(language, needle):
    """스펙에 검증 가설이 없으면 페인포인트 1순위를 가설로 쓰라고 말해야 한다.

    프롬프트는 "명세의 검증 가설·성공 기준"을 근거로 문항을 만들라고 하는데,
    `## Validation Hypothesis`는 Path A.1 `prototype-spec.md`에만 있고
    (prototype-validation.md:70) Path B의 `PROTOTYPE-{slug}.md` 양식에는
    없다(prototype-md-format.md). Path B에서는 그 지시가 붙을 데가 없어 모델이
    가설을 지어낸다.
    """
    p = build_prompt(MD, context=FULL, language=language)
    assert needle in p.lower()


def test_english_context_prompt_keeps_its_instructions_in_english():
    """근거 문서가 한국어여도 지시문은 영어로 남아야 한다.

    이 모듈이 이미 겪은 실패다 — `{md}`로 실린 명세가 더 가깝고 구체적인 신호라
    모델이 그 언어를 따라갔다(2026-08-05). 문서를 둘 더 실으면 그 압력이 커진다.
    """
    p = build_prompt(MD, context=FULL, language="en")
    body = p.replace(MD, "").replace(PAIN_POINTS, "").replace(BUSINESS_CONTEXT, "")
    assert not any("가" <= c <= "힣" for c in body), body[:600]


async def test_build_questionnaire_passes_the_context_through():
    agent = FakeAgent(json.dumps(VALID, ensure_ascii=False))
    await build_questionnaire(MD, agent, token="t", project_id="p", slug="s",
                              now="n", context=FULL)
    assert "조정 사유가 진료과에 전달되지 않는다" in agent.prompts[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["ko", "en"])
async def test_build_questionnaire_records_the_language(language):
    async def agent(prompt):
        return ('{"title": "T", "hypothesis": "H", "questions": '
                '[{"id": "q1", "text": "Q", "type": "text", "required": false}]}')

    qn = await build_questionnaire("# spec", agent, token="tok",
                                   project_id="p1", slug="demo",
                                   now="2026-08-03T00:00:00+00:00",
                                   language=language)
    # 언어를 questionnaire에 기록해야 공개 응답 페이지가 그 언어로 그릴 수 있다.
    assert qn.language == language
