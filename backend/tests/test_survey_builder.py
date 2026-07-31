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
