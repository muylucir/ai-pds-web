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


def test_results_markdown_is_english_for_an_english_survey():
    """리포트는 aiplc-docs/**에 생성되는 산출물이므로 UI 언어가 아니라
    프로젝트 언어를 따른다."""
    from pathfinder.survey.store import _results_markdown
    from pathfinder.survey.models import Questionnaire, Rollup

    qn = Questionnaire(
        token="t", status="open", slug="demo", project_id="p1",
        created_at="2026-08-03T00:00:00+00:00", closed_at=None, language="en",
        title="T", hypothesis="H",
        questions=[{"id": "q1", "text": "Q1", "type": "text", "required": False}])
    rollup = Rollup(count=0, per_question={}, rebuilt_at="2026-08-03T00:00:00+00:00")
    md = _results_markdown(qn, [], rollup, "2026-08-03T00:00:00+00:00", "en")
    assert "Prototype" in md or "prototype" in md
    assert not any("가" <= c <= "힣" for c in md), md[:400]


def test_results_markdown_stays_korean_by_default():
    from pathfinder.survey.store import _results_markdown
    from pathfinder.survey.models import Questionnaire, Rollup

    qn = Questionnaire(
        token="t", status="open", slug="demo", project_id="p1",
        created_at="2026-08-03T00:00:00+00:00", closed_at=None,
        title="T", hypothesis="H",
        questions=[{"id": "q1", "text": "Q1", "type": "text", "required": False}])
    rollup = Rollup(count=0, per_question={}, rebuilt_at="2026-08-03T00:00:00+00:00")
    md = _results_markdown(qn, [], rollup, "2026-08-03T00:00:00+00:00", "ko")
    assert "프로토타입" in md


def test_results_markdown_keeps_the_rule_headings_in_english_for_both():
    """prototype-validation.md Step 6이 정한 섹션 이름은 양쪽 언어에서 영어다 —
    룰이 그 이름으로 문서를 찾는다."""
    from pathfinder.survey.store import _results_markdown
    from pathfinder.survey.models import Questionnaire, Rollup

    qn = Questionnaire(
        token="t", status="open", slug="demo", project_id="p1",
        created_at="2026-08-03T00:00:00+00:00", closed_at=None,
        title="T", hypothesis="H",
        questions=[{"id": "q1", "text": "Q1", "type": "text", "required": False}])
    rollup = Rollup(count=0, per_question={}, rebuilt_at="2026-08-03T00:00:00+00:00")
    for language in ("ko", "en"):
        md = _results_markdown(qn, [], rollup, "2026-08-03T00:00:00+00:00", language)
        for heading in ("# Validation Results", "## Feedback Sources",
                        "## Theme Analysis", "## Pain Point Mapping",
                        "## Build Decision"):
            assert heading in md, (language, heading)


async def test_synthesize_writes_the_report_in_the_stores_language():
    """스토어 → 리포트 배선. 이 홉이 끊기면 영어 프로젝트도 한국어 리포트를
    받는다 — 에러는 없고, aiplc-docs/**에 잘못된 언어의 산출물이 남는다."""
    project_s3, root_s3 = FakeS3Store(), FakeS3Store()
    store = SurveyStore(project_s3, root_s3, slug=SLUG, project_id=PID,
                        language="en")
    # 설문 자체의 데이터(제목·가설·문항)도 영어여야 리포트에 한글이 남지
    # 않는다 — 영어 프로젝트에서는 build_questionnaire가 그렇게 만든다.
    await store.save_questionnaire(_qn(
        language="en", title="Validation survey", hypothesis="H",
        questions=[Question(id="q1", text="Useful?", type="scale")]))
    await store.synthesize_results()
    from pathfinder.survey.store import RESULTS_MD_KEY
    md = project_s3.blobs[RESULTS_MD_KEY]
    assert "Prototype" in str(md)
    assert not any("가" <= c <= "힣" for c in str(md)), str(md)[:300]


async def test_questionnaire_markdown_follows_the_stores_language():
    project_s3, root_s3 = FakeS3Store(), FakeS3Store()
    store = SurveyStore(project_s3, root_s3, slug=SLUG, project_id=PID,
                        language="en")
    await store.save_questionnaire(_qn(
        language="en", title="Validation survey", hypothesis="H",
        questions=[Question(id="q1", text="Useful?", type="scale")]))
    from pathfinder.survey.store import questionnaire_md_key
    md = str(project_s3.blobs[questionnaire_md_key(SLUG)])
    assert "Validation hypothesis" in md
    assert "검증 가설" not in md


def test_report_labels_fall_back_to_korean_for_an_unknown_language():
    """손상된 매니페스트가 임의 문자열을 실어 와도 리포트가 한국어로 나온다 —
    이 기능 이전 모든 프로젝트의 언어가 그것이다."""
    from pathfinder.survey.report_labels import labels
    assert labels("klingon") == labels("ko")
    assert labels("") == labels("ko")
