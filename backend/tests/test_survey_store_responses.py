import json
import pytest
from pathfinder.survey.models import Question, Questionnaire, SurveyResponse
from pathfinder.survey.store import SurveyStore, rollup_key, responses_prefix
from fakes.in_memory_s3 import FakeS3Store

PID, SLUG, NOW = "p1", "demo", "2026-07-25T00:00:00Z"
QUESTIONS = [Question(id="q1", text="유용?", type="scale"),
             Question(id="q2", text="자유", type="text", required=False)]


def _qn(status="open", closed_at=None):
    return Questionnaire(token="tok", status=status, slug=SLUG, project_id=PID,
                         created_at=NOW, closed_at=closed_at, title="t",
                         hypothesis="h", questions=QUESTIONS)


async def _seeded(n=0, status="open", closed_at=None):
    project_s3, root_s3 = FakeS3Store(), FakeS3Store()
    store = SurveyStore(project_s3, root_s3, slug=SLUG, project_id=PID)
    await store.save_questionnaire(_qn(status, closed_at))
    for i in range(n):
        await store.append_response(SurveyResponse(
            response_id=f"r{i}", submitted_at=NOW, answers={"q1": 4, "q2": f"의견{i}"}))
    return store, project_s3, root_s3


async def test_append_writes_one_object_per_response():
    store, project_s3, _ = await _seeded(3)
    keys = [k for k in project_s3.blobs if k.startswith(responses_prefix(SLUG))]
    assert len(keys) == 3
    assert await store.response_count() == 3


async def test_refresh_rollup_saves_aggregate():
    store, project_s3, _ = await _seeded(2)
    ru = await store.refresh_rollup(NOW)
    assert ru.count == 2 and ru.per_question["q1"].mean == 4.0
    stored = json.loads(project_s3.blobs[rollup_key(SLUG)])
    assert stored["count"] == 2


async def test_get_rollup_rebuilds_when_absent():
    store, project_s3, _ = await _seeded(2)
    assert rollup_key(SLUG) not in project_s3.blobs
    ru = await store.get_rollup(NOW)
    assert ru.count == 2
    assert rollup_key(SLUG) in project_s3.blobs   # cached for next read


async def test_get_rollup_rebuilds_on_count_mismatch():
    # A stale cache (e.g. a rollup write that failed after the response PUT
    # succeeded) must not report wrong numbers.
    store, project_s3, _ = await _seeded(3)
    await store.refresh_rollup(NOW)
    project_s3.blobs[rollup_key(SLUG)] = json.dumps(
        {"count": 1, "rebuilt_at": NOW,
         "per_question": {"q1": {"type": "scale", "n": 1, "mean": 1.0,
                                 "distribution": {"1": 1, "2": 0, "3": 0,
                                                  "4": 0, "5": 0}},
                          "q2": {"type": "text", "n": 0, "samples": []}}})
    ru = await store.get_rollup(NOW)
    assert ru.count == 3
    assert ru.per_question["q1"].mean == 4.0


async def test_get_rollup_uses_cache_when_count_matches():
    store, project_s3, _ = await _seeded(2)
    await store.refresh_rollup(NOW)
    # Sabotage the response objects: if the cache is honoured, the rollup's
    # numbers stay as cached (proving no per-object rebuild happened).
    for k in list(project_s3.blobs):
        if k.startswith(responses_prefix(SLUG)):
            project_s3.blobs[k] = json.dumps(
                {"response_id": "x", "submitted_at": NOW, "answers": {"q1": 1}})
    ru = await store.get_rollup(NOW)
    assert ru.per_question["q1"].mean == 4.0   # from cache, not rebuilt


async def test_archive_moves_questionnaire_responses_and_rollup():
    store, project_s3, _ = await _seeded(2, status="closed", closed_at="2026-07-26T00:00:00Z")
    await store.refresh_rollup(NOW)
    await store.archive_current()

    live = [k for k in project_s3.blobs if k.startswith(responses_prefix(SLUG))]
    assert live == []                      # old responses no longer aggregate
    archived = [k for k in project_s3.blobs
                if "/archive/2026-07-26T00:00:00Z/" in k]
    assert any(k.endswith("questionnaire.json") for k in archived)
    assert any("/responses/" in k for k in archived)
    assert any(k.endswith("rollup.json") for k in archived)


async def test_archive_requires_closed_survey():
    store, _, _ = await _seeded(1, status="open")
    with pytest.raises(ValueError):
        await store.archive_current()


async def test_responses_csv_has_question_headers_and_rows():
    store, _, _ = await _seeded(2)
    csv_text = await store.responses_csv()
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("response_id,submitted_at,")
    assert "유용?" in lines[0] and "자유" in lines[0]
    assert len(lines) == 3            # header + 2 responses


async def test_responses_csv_quotes_embedded_commas_and_quotes():
    store, _, _ = await _seeded(0)
    await store.append_response(SurveyResponse(
        response_id="r1", submitted_at=NOW,
        answers={"q1": 5, "q2": 'a,b and "quoted"'}))
    csv_text = await store.responses_csv()
    import csv as _csv, io
    rows = list(_csv.reader(io.StringIO(csv_text)))
    assert rows[1][-1] == 'a,b and "quoted"'   # survives a round-trip
