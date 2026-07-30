import json
import pytest
from pathfinder.survey.models import Question, Questionnaire
from pathfinder.survey.store import SurveyStore
from fakes.in_memory_s3 import FakeS3Store

PID, SLUG, TOKEN = "p1", "demo", "tok-abc"


def _qn(**kw):
    base = dict(token=TOKEN, status="open", slug=SLUG, project_id=PID,
                created_at="2026-07-25T00:00:00Z", closed_at=None,
                title="검증 설문", hypothesis="가설",
                questions=[Question(id="q1", text="유용?", type="scale")])
    return Questionnaire(**{**base, **kw})


def _store():
    project_s3, root_s3 = FakeS3Store(), FakeS3Store()
    return SurveyStore(project_s3, root_s3, slug=SLUG, project_id=PID), project_s3, root_s3


class _PutFailsS3(FakeS3Store):
    """Root store whose writes always fail -- the shape of the observed
    AccessDenied on `surveys/by-token/` when the deploy role's S3 policy
    covered only `projects/*` and `sessions/*`."""

    async def put(self, key: str, content: str) -> None:
        raise PermissionError(key)


async def test_save_leaves_no_questionnaire_when_token_index_write_fails():
    """The token index must be written BEFORE the questionnaire.

    A questionnaire that exists with no index is the one unrecoverable state:
    `create_survey` sees `status == "open"` and refuses with 409 for good, so
    the prototype can never get a survey again -- while the survey it refuses
    to replace is itself unusable, since `/survey/{token}` cannot resolve a
    token that was never indexed. Writing the index first means a failure here
    leaves nothing behind and the user's retry just works.
    """
    project_s3, root_s3 = FakeS3Store(), _PutFailsS3()
    store = SurveyStore(project_s3, root_s3, slug=SLUG, project_id=PID)

    with pytest.raises(PermissionError):
        await store.save_questionnaire(_qn())

    assert f"prototypes/{SLUG}/survey/questionnaire.json" not in project_s3.blobs


async def test_save_writes_questionnaire_token_index_and_md():
    store, project_s3, root_s3 = _store()
    await store.save_questionnaire(_qn())

    saved = json.loads(project_s3.blobs[f"prototypes/{SLUG}/survey/questionnaire.json"])
    assert saved["token"] == TOKEN

    index = json.loads(root_s3.blobs[f"surveys/by-token/{TOKEN}.json"])
    assert index == {"project_id": PID, "slug": SLUG}

    # Human-readable copy must land under aiplc-docs/ -- the artifacts viewer
    # only serves that subtree.
    md_key = f"aiplc-docs/discovery/prototypes/{SLUG}/validation-questionnaire.md"
    assert "유용?" in project_s3.blobs[md_key]


async def test_load_roundtrip():
    store, _, _ = _store()
    await store.save_questionnaire(_qn())
    got = await store.load_questionnaire()
    assert got.token == TOKEN and got.questions[0].id == "q1"


async def test_load_missing_raises():
    store, _, _ = _store()
    with pytest.raises(FileNotFoundError):
        await store.load_questionnaire()


async def test_close_sets_status_and_is_idempotent():
    store, _, _ = _store()
    await store.save_questionnaire(_qn())
    closed = await store.close()
    assert closed.status == "closed" and closed.closed_at
    first_closed_at = closed.closed_at

    again = await store.close()
    assert again.status == "closed"
    assert again.closed_at == first_closed_at  # must not be bumped


async def test_resolve_token():
    store, _, root_s3 = _store()
    await store.save_questionnaire(_qn())
    assert await SurveyStore.resolve_token(root_s3, TOKEN) == (PID, SLUG)


async def test_resolve_unknown_token_raises():
    _, _, root_s3 = _store()
    with pytest.raises(FileNotFoundError):
        await SurveyStore.resolve_token(root_s3, "nope")


def test_public_url_path():
    assert SurveyStore.public_url_path("abc") == "/survey/abc"
