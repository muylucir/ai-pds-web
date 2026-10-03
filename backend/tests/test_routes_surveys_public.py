import json
import pytest
from fastapi.testclient import TestClient

import aipds.app as app_module
from aipds.survey.models import Question, Questionnaire
from aipds.survey.store import SurveyStore, responses_prefix
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


def test_get_token_indexed_without_a_questionnaire_404(env):
    """An index entry whose questionnaire does not exist must 404, not 500.

    This is the leftover `save_questionnaire` can produce by design: it writes
    the token index FIRST so that a failure part-way through leaves no
    unusable-but-unreplaceable survey behind (see that method's docstring).
    The cost is an index entry pointing at nothing, and it has to be an
    ordinary "survey not found" -- the token grants access to no data, and a
    500 here would turn a harmless leftover into a broken public link.
    """
    import asyncio
    asyncio.get_event_loop().run_until_complete(
        env["root_s3"].put("surveys/by-token/orphan-tok.json",
                           json.dumps({"project_id": PID, "slug": "gone"})))

    assert client.get("/survey/orphan-tok").status_code == 404
    assert client.post("/survey/orphan-tok",
                       json={"answers": {"q1": 3}}).status_code == 404


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
    import aipds.routes.surveys_public as pub
    monkeypatch.setattr(pub, "MAX_RESPONSES", 1)
    client.post(f"/survey/{TOKEN}", json={"answers": {"q1": 4, "q2": "A"}})
    resp = client.post(f"/survey/{TOKEN}", json={"answers": {"q1": 4, "q2": "A"}})
    assert resp.status_code == 429


def test_submitting_does_not_rebuild_the_rollup(env, monkeypatch):
    """제출은 응답 PUT 하나로 끝난다. 집계 재계산은 응답 전부를 다시 읽으므로
    제출마다 하면 N번째 제출이 GET N회가 된다 — 인증 없는 경로에서."""
    calls = []

    async def counting(self, *a, **k):
        calls.append(1)
        raise AssertionError("submit must not rebuild the rollup")
    monkeypatch.setattr(SurveyStore, "refresh_rollup", counting)
    resp = client.post(f"/survey/{TOKEN}", json={"answers": {"q1": 4, "q2": "A"}})
    assert resp.status_code == 204
    assert calls == []
    keys = [k for k in env["project_s3"].blobs
            if k.startswith(responses_prefix(SLUG))]
    assert len(keys) == 1


def test_the_dashboard_rollup_counts_a_fresh_submission(env):
    """제출이 집계를 갱신하지 않아도 대시보드가 읽는 집계는 맞아야 한다 —
    `get_rollup`이 응답 수가 어긋난 캐시를 다시 만든다."""
    client.post(f"/survey/{TOKEN}", json={"answers": {"q1": 4, "q2": "A"}})
    client.post(f"/survey/{TOKEN}", json={"answers": {"q1": 5, "q2": "A"}})
    import asyncio
    rollup = asyncio.get_event_loop().run_until_complete(env["store"].get_rollup())
    assert rollup.count == 2


def test_post_rejects_oversized_body_before_parsing(env):
    """The authoritative byte cap runs after Starlette buffers and parses the
    body, so an anonymous caller could make us parse megabytes before the
    400. A Content-Length short-circuit (same pattern as routes/uploads.py)
    rejects it up front."""
    huge = "가" * 200_000  # ~600KB utf-8, far over MAX_BODY_BYTES
    resp = client.post(f"/survey/{TOKEN}", json={"answers": {"q3": huge}})
    assert resp.status_code == 413
    assert resp.json()["detail"] == "response too large"


def test_post_still_accepts_a_normal_sized_body(env):
    """The pre-check must not reject legitimate submissions."""
    resp = client.post(f"/survey/{TOKEN}",
                       json={"answers": {"q1": 4, "q2": "A", "q3": "좋았습니다"}})
    assert resp.status_code == 204
