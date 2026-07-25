import json
import pytest
from fastapi.testclient import TestClient

import pathfinder.app as app_module
from pathfinder.survey.models import Question, Questionnaire, SurveyResponse
from pathfinder.survey.store import SurveyStore
from pathfinder.workspace import Workspace
from fakes.fake_runner import FakeRunner
from fakes.in_memory_s3 import FakeS3Store

client = TestClient(app_module.app)

PID, SLUG = "survey-route-test", "demo"
SPEC_KEY = f"aiplc-docs/discovery/prototypes/{SLUG}/PROTOTYPE-{SLUG}.md"
GOOD_JSON = json.dumps({
    "title": "검증 설문", "hypothesis": "가설",
    "questions": [{"id": "q1", "text": "유용?", "type": "scale", "required": True},
                  {"id": "q2", "text": "개선점", "type": "text", "required": False}],
}, ensure_ascii=False)


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("PATHFINDER_S3_BUCKET", "")
    project_s3, root_s3 = FakeS3Store(), FakeS3Store()
    project_s3.blobs[SPEC_KEY] = "# PROTOTYPE demo\n검증 가설: 판독 시간 단축"

    async def fake_make_workspace(pid):
        return Workspace(FakeRunner(FakeS3Store()))

    monkeypatch.setattr(app_module, "make_workspace", fake_make_workspace)
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: project_s3)
    monkeypatch.setattr(app_module, "surveys_root_s3_factory", lambda: root_s3)
    monkeypatch.setattr(
        app_module, "survey_store_factory",
        lambda pid, slug: SurveyStore(project_s3, root_s3, slug=slug, project_id=pid))

    replies = [GOOD_JSON]

    def agent_factory():
        async def call(prompt):
            return replies.pop(0)
        return call

    monkeypatch.setattr(app_module, "questionnaire_agent_factory", agent_factory)

    resp = client.post("/projects", json={"project_id": PID})
    assert resp.status_code in (200, 201, 409)
    yield {"project_s3": project_s3, "root_s3": root_s3, "replies": replies}
    app_module.registry.remove(PID)


def _create(env):
    return client.post(f"/projects/{PID}/prototypes/{SLUG}/survey")


def test_create_returns_token_and_public_url(env):
    resp = _create(env)
    assert resp.status_code == 201
    body = resp.json()
    assert len(body["token"]) >= 32
    assert body["url"] == f"/survey/{body['token']}"
    assert [q["id"] for q in body["questions"]] == ["q1", "q2"]


def test_create_404_when_prototype_spec_missing(env):
    del env["project_s3"].blobs[SPEC_KEY]
    assert _create(env).status_code == 404


def test_create_409_when_open_survey_exists(env):
    assert _create(env).status_code == 201
    env["replies"].append(GOOD_JSON)
    assert _create(env).status_code == 409


def test_create_502_sanitized_when_generation_fails(env, monkeypatch):
    def agent_factory():
        async def call(prompt):
            raise RuntimeError("AKIA-secret boom")
        return call
    monkeypatch.setattr(app_module, "questionnaire_agent_factory", agent_factory)
    resp = _create(env)
    assert resp.status_code == 502
    assert "AKIA" not in resp.text


def test_create_after_close_archives_previous(env):
    assert _create(env).status_code == 201
    client.post(f"/projects/{PID}/prototypes/{SLUG}/survey/close")
    env["replies"].append(GOOD_JSON)
    assert _create(env).status_code == 201
    assert any("/archive/" in k for k in env["project_s3"].blobs)


def test_get_returns_questionnaire_and_rollup(env):
    _create(env)
    body = client.get(f"/projects/{PID}/prototypes/{SLUG}/survey").json()
    assert body["questionnaire"]["status"] == "open"
    assert body["rollup"]["count"] == 0


def test_get_404_without_survey(env):
    assert client.get(f"/projects/{PID}/prototypes/{SLUG}/survey").status_code == 404


def test_close_is_204_and_idempotent(env):
    _create(env)
    url = f"/projects/{PID}/prototypes/{SLUG}/survey/close"
    assert client.post(url).status_code == 204
    assert client.post(url).status_code == 204


def test_csv_export(env):
    _create(env)
    store = app_module.survey_store_factory(PID, SLUG)
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.run_until_complete(store.append_response(
        SurveyResponse(response_id="r1", submitted_at="2026-07-25T00:00:00Z",
                       answers={"q1": 5, "q2": "좋음"})))
    resp = client.get(f"/projects/{PID}/prototypes/{SLUG}/survey/responses.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "유용?" in resp.text and "좋음" in resp.text


def test_unknown_project_404(env):
    assert client.post("/projects/nope/prototypes/x/survey").status_code == 404
