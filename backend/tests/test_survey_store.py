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
    key, _ = await store.synthesize_results()
    md = project_s3.blobs[key]
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


# ---- validation-results.md 를 슬러그별로 쪼갠다 (2026-08-20) ----
#
# **왜 바꾸는가.** `RESULTS_MD_KEY`가 슬러그 없는 모듈 상수였다
# (`aiplc-docs/discovery/prototype/validation-results.md`). Path B는 프로토타입을
# N개 만드는데(실측 test2222: 3개, 스펙 3개·빌드 3개 완료) 취합 라우트는
# 슬러그별(`POST .../prototypes/{slug}/survey/synthesize`)이므로, 셋을 취합하면
# 셋이 같은 키를 덮어쓰고 마지막 것만 남았다 — 오류 없이 틀린 결과다.
#
# 단수 경로의 근거는 "prototype-validation.md Step 6이 기대하는 위치이고 이후
# product-strategy가 거기를 본다"였다. 그런데 그 문서는 제목부터 "Path A.1 -
# Single Solution"이고 본문이 "ORIGINAL single-prototype flow"라고 명시한다.
# Path B가 타는 `prototype-building.md`에는 검증 단계가 아예 없고
# (끝이 "Proceed to: Product Strategy"), `use-case-prioritization.md`에는 검증
# 언급이 0회다. 즉 3개 프로토타입 프로젝트에 단수 경로를 요구하는 상류가 없다.
#
# **A.1의 경로는 반드시 그대로 남아야 한다** — 거기서는 상류가 실제로 그 경로를
# 규정하고 product-strategy가 읽는다. `layout.artifact_dir`이 이미 그 분기를
# 갖고 있어서 한 식으로 둘 다 만족한다.

from pathfinder.proto import layout as _layout          # noqa: E402
from pathfinder.survey.store import results_md_key      # noqa: E402


def test_results_key_for_a_single_prototype_is_the_rule_declared_path():
    """Path A.1은 오늘과 바이트 단위로 같아야 한다.

    `prototype-validation.md`가 선언하는 산출물이고 이후 product-strategy
    스테이지가 그 경로를 읽는다. 여기가 바뀌면 그 스테이지가 검증 결과를 못 찾고,
    실패는 조용하다.
    """
    assert results_md_key(_layout.SINGLE_ID) == \
        "aiplc-docs/discovery/prototype/validation-results.md"


def test_results_key_for_path_b_carries_the_slug():
    assert results_md_key("customer-inquiry-triage") == \
        ("aiplc-docs/discovery/prototypes/customer-inquiry-triage"
         "/validation-results.md")


def test_results_key_sits_beside_the_questionnaire_copy():
    """설문지 사본과 같은 디렉터리다. 갈라지면 삭제·아카이브 경로가 한쪽을
    잊는다 — `layout.artifact_dir`이 존재하는 이유가 그것이다."""
    from pathfinder.survey.store import questionnaire_md_key
    for slug in (_layout.SINGLE_ID, "flight-disruption-notice"):
        assert (results_md_key(slug).rsplit("/", 1)[0]
                == questionnaire_md_key(slug).rsplit("/", 1)[0])


async def test_three_prototypes_synthesize_without_overwriting_each_other():
    """실측 test2222의 모양이다 — 프로토타입 3개, 취합 3번.

    종전에는 셋이 한 키를 덮어써서 마지막 프로토타입의 결과만 남았다.
    """
    project_s3, root_s3 = FakeS3Store(), FakeS3Store()
    slugs = ("customer-inquiry-triage", "flight-disruption-notice",
             "maintenance-fault-diagnosis")
    for slug in slugs:
        store = SurveyStore(project_s3, root_s3, slug=slug, project_id=PID)
        await store.save_questionnaire(_qn(slug=slug, token=f"tok-{slug}",
                                           title=f"{slug} 검증"))
        key, _ = await store.synthesize_results()
        assert key == results_md_key(slug)

    written = [k for k in project_s3.blobs if k.endswith("validation-results.md")]
    assert len(written) == 3, written
    # 각 파일이 자기 프로토타입의 설문 제목을 담아야 한다 — 덮어썼다면 셋이 같다.
    for slug in slugs:
        assert f"{slug} 검증" in project_s3.blobs[results_md_key(slug)]
