import asyncio
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

    def agent_factory(_pid):
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
    def agent_factory(_pid):
        async def call(prompt):
            raise RuntimeError("AKIA-secret boom")
        return call
    monkeypatch.setattr(app_module, "questionnaire_agent_factory", agent_factory)
    resp = _create(env)
    assert resp.status_code == 502
    assert "AKIA" not in resp.text


def _capture_prompts(monkeypatch, replies):
    """agent_factory를 프롬프트를 기록하는 것으로 갈아끼운다."""
    seen = []

    def agent_factory(_pid):
        async def call(prompt):
            seen.append(prompt)
            return replies.pop(0)
        return call

    monkeypatch.setattr(app_module, "questionnaire_agent_factory", agent_factory)
    return seen


ENVISION = "aiplc-docs/discovery/envision/"


def test_create_carries_the_envision_evidence_into_the_prompt(env, monkeypatch):
    """설문 문항은 스펙 요약이 아니라 그 요약이 나온 근거로 만들어야 한다.

    스펙의 Problem Statement·Business Value는 한두 줄이고, 페인포인트별 심각도·
    빈도·우선순위와 업종·현행 업무 방식은 Envision 산출물에만 있다.
    """
    s3 = env["project_s3"]
    s3.blobs[ENVISION + "pain-point-analysis.md"] = "조정 사유가 전달되지 않는다"
    s3.blobs[ENVISION + "business-context.md"] = "400병상 2차 종합병원"
    seen = _capture_prompts(monkeypatch, [GOOD_JSON])

    assert _create(env).status_code == 201
    assert "조정 사유가 전달되지 않는다" in seen[0]
    assert "400병상 2차 종합병원" in seen[0]


def test_create_succeeds_when_the_envision_evidence_is_absent(env, monkeypatch):
    """보조 문서가 없어도 설문은 만들어진다 — 스펙만으로도 성립한다."""
    seen = _capture_prompts(monkeypatch, [GOOD_JSON])
    assert _create(env).status_code == 201
    assert "# PROTOTYPE demo" in seen[0]


def test_create_ignores_the_business_context_question_file(env, monkeypatch):
    """질문지는 컨텍스트가 아니다.

    `business-context-questions.md`는 룰이 선언하는 질문지이고 본문이 선택지와
    `[Answer]:` 태그다 — 실으면 모델이 남의 질문 양식을 베낀다.
    """
    env["project_s3"].blobs[ENVISION + "business-context-questions.md"] = (
        "A) 제조\nB) 금융\nX) Other\n\n[Answer]: A")
    seen = _capture_prompts(monkeypatch, [GOOD_JSON])
    assert _create(env).status_code == 201
    assert "[Answer]: A" not in seen[0]


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


def test_synthesize_writes_rule_expected_results_path(env):
    """The aggregate must land at the path the rule defines (Step 6) and the
    later product-strategy stage reads — not the per-slug questionnaire tree."""
    _create(env)
    store = app_module.survey_store_factory(PID, SLUG)
    asyncio.run(store.append_response(SurveyResponse(
        response_id="r1", submitted_at="2026-07-25T00:00:01Z",
        answers={"q1": 5, "q2": "속도가 인상적입니다"})))
    asyncio.run(store.append_response(SurveyResponse(
        response_id="r2", submitted_at="2026-07-25T00:00:02Z",
        answers={"q1": 3, "q2": "정확도가 아쉽다"})))

    resp = client.post(f"/projects/{PID}/prototypes/{SLUG}/survey/synthesize")
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"] == "aiplc-docs/discovery/prototype/validation-results.md"
    assert body["response_count"] == 2

    md = env["project_s3"].blobs[body["path"]]
    assert "# Validation Results" in md
    assert f"**응답 수**: 2" in md
    # Quantitative aggregate present
    assert "평균 **4.0** / 5" in md
    # EVERY free-text answer verbatim (not the rollup's 20-sample cap)
    assert "속도가 인상적입니다" in md and "정확도가 아쉽다" in md
    # Judgment sections left for the PM, not machine-guessed
    assert "## Theme Analysis" in md and "## Pain Point Mapping" in md
    assert "## Build Decision" in md


def test_synthesize_404_without_survey(env):
    assert client.post(
        f"/projects/{PID}/prototypes/{SLUG}/survey/synthesize").status_code == 404


def test_synthesize_is_rerunnable_and_reflects_new_responses(env):
    """Re-running after more responses land must overwrite with fresh numbers."""
    _create(env)
    store = app_module.survey_store_factory(PID, SLUG)
    asyncio.run(store.append_response(SurveyResponse(
        response_id="a", submitted_at="2026-07-25T00:00:01Z", answers={"q1": 1})))
    first = client.post(f"/projects/{PID}/prototypes/{SLUG}/survey/synthesize").json()
    assert first["response_count"] == 1

    asyncio.run(store.append_response(SurveyResponse(
        response_id="b", submitted_at="2026-07-25T00:00:02Z", answers={"q1": 5})))
    second = client.post(f"/projects/{PID}/prototypes/{SLUG}/survey/synthesize").json()
    assert second["response_count"] == 2
    md = env["project_s3"].blobs[second["path"]]
    assert "**응답 수**: 2" in md
    assert "평균 **3.0** / 5" in md
