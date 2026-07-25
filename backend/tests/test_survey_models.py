import pytest
from pydantic import ValidationError
from pathfinder.survey.models import (Question, Questionnaire, SurveyResponse,
                                      Rollup, ScaleStat, ChoiceStat, TextStat)


def _q(**kw):
    base = {"id": "q1", "text": "유용했나요?", "type": "scale"}
    return Question(**{**base, **kw})


def test_choice_question_requires_options():
    # A choice question with no options is unanswerable -- reject at the model.
    with pytest.raises(ValidationError):
        Question(id="q1", text="어느 것?", type="choice", options=[])


def test_scale_and_text_reject_options():
    # scale is fixed 1-5 and text is free-form: options would be meaningless
    # and would silently render as a choice list in the public form.
    with pytest.raises(ValidationError):
        Question(id="q1", text="t", type="scale", options=["1", "2"])
    with pytest.raises(ValidationError):
        Question(id="q2", text="t", type="text", options=["a"])


def test_choice_question_accepts_options():
    q = Question(id="q1", text="어느 것?", type="choice", options=["A", "B"])
    assert q.options == ["A", "B"]
    assert q.required is True


def test_questionnaire_roundtrips_json():
    qn = Questionnaire(
        token="t" * 43, status="open", slug="demo", project_id="p1",
        created_at="2026-07-25T00:00:00Z", closed_at=None,
        title="검증 설문", hypothesis="가설", questions=[_q()])
    again = Questionnaire.model_validate_json(qn.model_dump_json())
    assert again.token == "t" * 43
    assert again.questions[0].text == "유용했나요?"


def test_questionnaire_rejects_duplicate_question_ids():
    # Duplicate ids would make the answers dict lose one question's response.
    with pytest.raises(ValidationError):
        Questionnaire(
            token="t", status="open", slug="s", project_id="p",
            created_at="x", closed_at=None, title="t", hypothesis="h",
            questions=[_q(id="q1"), _q(id="q1")])


def test_questionnaire_rejects_empty_questions():
    with pytest.raises(ValidationError):
        Questionnaire(token="t", status="open", slug="s", project_id="p",
                      created_at="x", closed_at=None, title="t",
                      hypothesis="h", questions=[])


def test_response_model():
    r = SurveyResponse(response_id="abc", submitted_at="2026-07-25T00:00:00Z",
                       answers={"q1": 4, "q2": "매우 유용"})
    assert r.answers["q1"] == 4


def test_rollup_model():
    ru = Rollup(count=2, rebuilt_at="2026-07-25T00:00:00Z", per_question={
        "q1": ScaleStat(n=2, mean=4.5, distribution={"1": 0, "2": 0, "3": 0,
                                                     "4": 1, "5": 1}),
        "q2": ChoiceStat(n=2, counts={"A": 2}),
        "q3": TextStat(n=1, samples=["좋았다"]),
    })
    assert ru.per_question["q1"].type == "scale"
    assert ru.per_question["q2"].type == "choice"
    assert ru.per_question["q3"].type == "text"
