import json
import pytest
from fastapi.testclient import TestClient

import pathfinder.app as app_module
from pathfinder.survey.models import Question, Questionnaire
from pathfinder.survey.store import SurveyStore, responses_prefix
from fakes.in_memory_s3 import FakeS3Store

client = TestClient(app_module.app)

PID, SLUG, TOKEN = "p-pub", "demo", "tok-public-123"
QUESTIONS = [Question(id="q1", text="유용?", type="scale"),
             Question(id="q2", text="어느 것?", type="choice", options=["A", "B"]),
             Question(id="q3", text="개선점", type="text", required=False)]


def _qn(status="open", closed_at=None):
    return Questionnaire(token=TOKEN, status=status, slug=SLUG, project_id=PID,
                         created_at="2026-07-25T00:00:00Z", closed_at=closed_at,
                         title="검증 설문", hypothesis="가설", questions=QUESTIONS)


@pytest.fixture()
def env(monkeypatch):
    project_s3, root_s3 = FakeS3Store(), FakeS3Store()
    monkeypatch.setattr(app_module, "surveys_root_s3_factory", lambda: root_s3)
    monkeypatch.setattr(
        app_module, "survey_store_factory",
        lambda pid, slug: SurveyStore(project_s3, root_s3, slug=slug, project_id=pid))
    store = SurveyStore(project_s3, root_s3, slug=SLUG, project_id=PID)
    import asyncio
    asyncio.get_event_loop().run_until_complete(store.save_questionnaire(_qn()))
    return {"project_s3": project_s3, "root_s3": root_s3, "store": store}


def _close(env):
    import asyncio
    asyncio.get_event_loop().run_until_complete(env["store"].close())


def test_get_returns_questions_only(env):
    body = client.get(f"/survey/{TOKEN}").json()
    assert body["title"] == "검증 설문"
    assert [q["id"] for q in body["questions"]] == ["q1", "q2", "q3"]
    # The public payload must never leak internal identifiers or aggregates.
    raw = json.dumps(body)
    assert PID not in raw and SLUG not in raw
    assert "rollup" not in body and "token" not in body


def test_get_unknown_token_404(env):
    assert client.get("/survey/nope").status_code == 404


def test_get_closed_survey_410(env):
    _close(env)
    assert client.get(f"/survey/{TOKEN}").status_code == 410


def test_post_stores_response(env):
    resp = client.post(f"/survey/{TOKEN}",
                       json={"answers": {"q1": 4, "q2": "A", "q3": "좋음"}})
    assert resp.status_code == 204
    keys = [k for k in env["project_s3"].blobs
            if k.startswith(responses_prefix(SLUG))]
    assert len(keys) == 1


def test_post_closed_survey_410(env):
    _close(env)
    assert client.post(f"/survey/{TOKEN}", json={"answers": {"q1": 4}}).status_code == 410


def test_post_rejects_unknown_question_key(env):
    resp = client.post(f"/survey/{TOKEN}", json={"answers": {"qZ": "x"}})
    assert resp.status_code == 400


def test_post_rejects_wrong_type_for_scale(env):
    assert client.post(f"/survey/{TOKEN}",
                       json={"answers": {"q1": "넷"}}).status_code == 400


def test_post_rejects_bool_for_scale(env):
    # pydantic coerces JSON true -> int 1 in a dict[str, str | int] field, so
    # a bool would silently look like a legitimate scale score of 1 by the
    # time the rollup sees it. AnswersBody uses dict[str, object] so the raw
    # bool survives to _validate_answers, which must reject it explicitly.
    assert client.post(f"/survey/{TOKEN}",
                       json={"answers": {"q1": True}}).status_code == 400


def test_post_rejects_out_of_range_scale(env):
    assert client.post(f"/survey/{TOKEN}",
                       json={"answers": {"q1": 9}}).status_code == 400


def test_post_rejects_option_not_offered(env):
    assert client.post(f"/survey/{TOKEN}",
                       json={"answers": {"q2": "Z"}}).status_code == 400


def test_post_rejects_missing_required_answer(env):
    # q1/q2 are required; a body with only the optional q3 must not count as a
    # response.
    assert client.post(f"/survey/{TOKEN}",
                       json={"answers": {"q3": "의견"}}).status_code == 400


def test_post_rejects_oversized_answer(env):
    big = "가" * 2001
    assert client.post(f"/survey/{TOKEN}",
                       json={"answers": {"q1": 4, "q2": "A", "q3": big}}
                       ).status_code == 413


def test_post_429_when_response_cap_reached(env, monkeypatch):
    import pathfinder.routes.surveys_public as pub
    monkeypatch.setattr(pub, "MAX_RESPONSES", 1)
    client.post(f"/survey/{TOKEN}", json={"answers": {"q1": 4, "q2": "A"}})
    resp = client.post(f"/survey/{TOKEN}", json={"answers": {"q1": 4, "q2": "A"}})
    assert resp.status_code == 429


def test_rollup_failure_does_not_fail_the_response(env, monkeypatch):
    # The response PUT is what commits; a rollup write failure must not lose
    # the respondent's submission (spec §3 cache contract).
    async def boom(*a, **k):
        raise RuntimeError("s3 down")
    monkeypatch.setattr(SurveyStore, "refresh_rollup", boom)
    resp = client.post(f"/survey/{TOKEN}", json={"answers": {"q1": 4, "q2": "A"}})
    assert resp.status_code == 204
    keys = [k for k in env["project_s3"].blobs
            if k.startswith(responses_prefix(SLUG))]
    assert len(keys) == 1
