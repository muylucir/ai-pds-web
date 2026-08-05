import json
import pytest
from pathfinder.survey.builder import build_prompt, build_questionnaire

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
