import json
import pytest
from aipds.survey.models import Question, Questionnaire
from aipds.survey.store import SurveyStore
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


def test_aggregate_markdown_is_english_for_an_english_survey():
    """The report is an artifact generated in aiplc-docs/**, so it follows the project language,
    not the UI language."""
    from aipds.survey.store import _aggregate_markdown
    from aipds.survey.models import Questionnaire, Rollup

    qn = Questionnaire(
        token=TOKEN, status="open", slug="demo", project_id="p1",
        created_at="2026-08-03T00:00:00+00:00", closed_at=None, language="en",
        title="T", hypothesis="H",
        questions=[{"id": "q1", "text": "Q1", "type": "text", "required": False}])
    rollup = Rollup(count=0, per_question={}, rebuilt_at="2026-08-03T00:00:00+00:00")
    md = _aggregate_markdown(qn, [], rollup, "2026-08-03T00:00:00+00:00", "en")
    assert "Prototype" in md or "prototype" in md
    assert not any("가" <= c <= "힣" for c in md), md[:400]


def test_aggregate_markdown_stays_korean_by_default():
    from aipds.survey.store import _aggregate_markdown
    from aipds.survey.models import Questionnaire, Rollup

    qn = Questionnaire(
        token=TOKEN, status="open", slug="demo", project_id="p1",
        created_at="2026-08-03T00:00:00+00:00", closed_at=None,
        title="T", hypothesis="H",
        questions=[{"id": "q1", "text": "Q1", "type": "text", "required": False}])
    rollup = Rollup(count=0, per_question={}, rebuilt_at="2026-08-03T00:00:00+00:00")
    md = _aggregate_markdown(qn, [], rollup, "2026-08-03T00:00:00+00:00", "ko")
    assert "프로토타입" in md


def test_aggregate_markdown_carries_no_step6_sections():
    """The aggregate holds only the collected data. The Step 6 judgment sections are written by the agent into the rules'
    `validation-results.md` (proto/layout.SURVEY_AGGREGATE).

    Putting them here, even as an empty template, would make the agent fill this file, and the next synthesis would
    overwrite that analysis — that is the shape seen on 2026-10-06 in ws-b, with the agent's analysis
    attached on top of an open survey.
    """
    from aipds.survey.store import _aggregate_markdown
    from aipds.survey.models import Questionnaire, Rollup

    qn = Questionnaire(
        token=TOKEN, status="open", slug="demo", project_id="p1",
        created_at="2026-08-03T00:00:00+00:00", closed_at=None,
        title="T", hypothesis="H",
        questions=[{"id": "q1", "text": "Q1", "type": "text", "required": False}])
    rollup = Rollup(count=0, per_question={}, rebuilt_at="2026-08-03T00:00:00+00:00")
    for language, title in (("ko", "# 설문 집계"), ("en", "# Survey Aggregate")):
        md = _aggregate_markdown(qn, [], rollup, "2026-08-03T00:00:00+00:00", language)
        assert md.startswith(title), (language, md[:80])
        for heading in ("# Validation Results", "## Feedback Sources",
                        "## Theme Analysis", "## Pain Point Mapping",
                        "## Build Decision"):
            assert heading not in md, (language, heading)
        # Guidance the agent reads: where the Step 6 synthesis goes.
        assert "validation-results.md" in md, language


def test_aggregate_markdown_carries_the_survey_window():
    """Both ends of the validation period must be in the aggregate. Without them the agent asks the PM when the
    survey started — that was confirmation question Q3 in ws-c on 2026-10-06."""
    from aipds.survey.store import _aggregate_markdown
    from aipds.survey.models import Rollup

    rollup = Rollup(count=0, per_question={}, rebuilt_at="2026-10-06T16:18:49+00:00")
    open_md = _aggregate_markdown(
        _qn(created_at="2026-10-06T16:16:07+00:00"), [], rollup,
        "2026-10-06T16:18:49+00:00", "ko")
    assert "**설문 시작 시각**: 2026-10-06T16:16:07+00:00" in open_md
    assert "설문 마감 시각" not in open_md  # an open survey has no close time

    closed_md = _aggregate_markdown(
        _qn(status="closed", created_at="2026-10-06T16:16:07+00:00",
            closed_at="2026-10-08T09:00:00+00:00"), [], rollup,
        "2026-10-08T09:01:00+00:00", "en")
    assert "**Survey started at**: 2026-10-06T16:16:07+00:00" in closed_md
    assert "**Survey closed at**: 2026-10-08T09:00:00+00:00" in closed_md


async def test_synthesize_writes_the_report_in_the_stores_language():
    """Store → report wiring. If this hop breaks, English projects get Korean reports too
    — there is no error, and an artifact in the wrong language is left in aiplc-docs/**."""
    project_s3, root_s3 = FakeS3Store(), FakeS3Store()
    store = SurveyStore(project_s3, root_s3, slug=SLUG, project_id=PID,
                        language="en")
    # The survey's own data (title, hypotheses, questions) must be English too for no Hangul to remain in the report
    # — in an English project, build_questionnaire makes it so.
    await store.save_questionnaire(_qn(
        language="en", title="Validation survey", hypothesis="H",
        questions=[Question(id="q1", text="Useful?", type="scale")]))
    key, _ = await store.synthesize_aggregate()
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
    from aipds.survey.store import questionnaire_md_key
    md = str(project_s3.blobs[questionnaire_md_key(SLUG)])
    assert "Validation hypothesis" in md
    assert "검증 가설" not in md


def test_report_labels_fall_back_to_korean_for_an_unknown_language():
    """Even if a corrupted manifest carries an arbitrary string, the report comes out in Korean —
    that is the language of every project from before this feature."""
    from aipds.survey.report_labels import labels
    assert labels("klingon") == labels("ko")
    assert labels("") == labels("ko")


# ---- The survey aggregate is per slug, and a different file from the rules' validation-results.md ----
#
# **Per slug (observed 2026-08-20, ws-n).** Path B makes N prototypes, and the synthesis
# route is per slug. When the key had no slug, synthesizing three made the three overwrite the same key
# and only the last one remained — a wrong result with no error.
#
# **Separated from the rules' file (observed 2026-10-06, ws-b).** Writing the aggregate into `validation-results.md`
# gives that file two owners: a re-synthesis overwrites the agent's Step 6 analysis,
# and a reset deletes the evidence Part 2 cites.

from aipds.proto import layout as _layout          # noqa: E402
from aipds.survey.store import aggregate_md_key    # noqa: E402


def test_aggregate_key_for_a_single_prototype_sits_beside_the_spec():
    assert aggregate_md_key(_layout.SINGLE_ID) == \
        "aiplc-docs/discovery/prototype/survey-aggregate.md"


def test_aggregate_key_for_path_b_carries_the_slug():
    assert aggregate_md_key("customer-inquiry-triage") == \
        ("aiplc-docs/discovery/prototypes/customer-inquiry-triage"
         "/survey-aggregate.md")


def test_aggregate_key_is_never_the_rules_results_file():
    for slug in (_layout.SINGLE_ID, "flight-disruption-notice"):
        assert not aggregate_md_key(slug).endswith("/validation-results.md")


def test_aggregate_key_sits_beside_the_questionnaire_copy():
    """Same directory as the questionnaire copy. If they split, the delete and archive paths forget
    one side — that is why `layout.artifact_dir` exists."""
    from aipds.survey.store import questionnaire_md_key
    for slug in (_layout.SINGLE_ID, "flight-disruption-notice"):
        assert (aggregate_md_key(slug).rsplit("/", 1)[0]
                == questionnaire_md_key(slug).rsplit("/", 1)[0])


async def test_synthesis_never_touches_the_rules_results_file():
    """A re-synthesis does not overwrite the agent's Step 6 analysis — the purpose of this separation."""
    project_s3, root_s3 = FakeS3Store(), FakeS3Store()
    store = SurveyStore(project_s3, root_s3, slug=_layout.SINGLE_ID,
                        project_id=PID)
    results = "aiplc-docs/discovery/prototype/validation-results.md"
    analysis = "# Validation Results\n## Theme Analysis\n(agent)"
    project_s3.blobs[results] = analysis
    await store.save_questionnaire(_qn(slug=_layout.SINGLE_ID))

    await store.synthesize_aggregate()
    await store.synthesize_aggregate()

    assert project_s3.blobs[results] == analysis


async def test_three_prototypes_synthesize_without_overwriting_each_other():
    """The shape observed in ws-n — 3 prototypes, 3 syntheses."""
    project_s3, root_s3 = FakeS3Store(), FakeS3Store()
    slugs = ("customer-inquiry-triage", "flight-disruption-notice",
             "maintenance-fault-diagnosis")
    for slug in slugs:
        store = SurveyStore(project_s3, root_s3, slug=slug, project_id=PID)
        await store.save_questionnaire(_qn(slug=slug, token=f"tok-{slug}",
                                           title=f"{slug} 검증"))
        key, _ = await store.synthesize_aggregate()
        assert key == aggregate_md_key(slug)

    written = [k for k in project_s3.blobs if k.endswith("survey-aggregate.md")]
    assert len(written) == 3, written
    # Each file must hold its own prototype's survey title — if overwritten, all three would be the same.
    for slug in slugs:
        assert f"{slug} 검증" in project_s3.blobs[aggregate_md_key(slug)]


# ---- Aggregate split by persona ----

def _persona_md(language="ko"):
    from aipds.survey.models import SurveyResponse
    from aipds.survey.rollup import build_rollup
    from aipds.survey.store import _aggregate_markdown

    other = "해당 없음 / 기타" if language == "ko" else "None of these / Other"
    qn = _qn(language=language, questions=[
        Question(id="persona", text="역할?", type="choice",
                 options=["점포 관리자", "본사 MD", other], persona=True),
        Question(id="q1", text="시간이 줄까?", type="scale"),
        Question(id="q2", text="유용한 기능?", type="choice",
                 options=["대시보드", "알림", "사용하지 않았다"]),
        Question(id="q3", text="개선점", type="text", required=False),
    ])
    rows = [("1", "점포 관리자", 3, "알림", "승인 단계가 없다"),
            ("2", "점포 관리자", 2, "사용하지 않았다", ""),
            ("3", "본사 MD", 5, "대시보드", "깔끔하다"),
            ("4", "점포 관리자", 3, "알림", "매장별 보기")]
    responses = [SurveyResponse(response_id=rid, submitted_at=f"2026-07-25T00:00:0{rid}Z",
                                answers={"persona": p, "q1": s, "q2": c,
                                         **({"q3": t} if t else {})})
                 for rid, p, s, c, t in rows]
    rollup = build_rollup(qn.questions, responses, "now")
    return _aggregate_markdown(qn, responses, rollup, "now", language)


def test_aggregate_lists_every_persona_even_those_who_did_not_respond():
    # A persona with 0 responses shows which of the target users were not reached.
    md = _persona_md()
    assert "- **응답자 구성**: 점포 관리자 3 · 본사 MD 1 · 해당 없음 / 기타 0" in md


def test_aggregate_splits_a_scale_question_by_persona():
    """With only the overall average, 'admins 5 points, actual users 2 points' collapses into one number."""
    md = _persona_md()
    section = md.split("### Q2.")[1].split("###")[0]
    assert "| 전체 | 4 | 3.25 | 1 | 0 | 2 | 1 | 0 |" in section
    assert "| 점포 관리자 | 3 | 2.67 | 0 | 0 | 2 | 1 | 0 |" in section
    assert "| 본사 MD | 1 | 5.0 | 1 | 0 | 0 | 0 | 0 |" in section
    # Personas with no responses get no row — the header layout already shows them.
    assert "| 해당 없음 / 기타 |" not in section


def test_aggregate_splits_a_choice_question_by_persona():
    md = _persona_md()
    section = md.split("### Q3.")[1].split("###")[0]
    assert "| 선택지 | 응답 수 | 비율 | 점포 관리자 (응답 3건) | 본사 MD (응답 1건) |" in section
    assert "| 알림 | 2 | 50% | 2 | 0 |" in section
    assert "| 대시보드 | 1 | 25% | 0 | 1 |" in section


def test_aggregate_does_not_split_the_persona_question_by_itself():
    md = _persona_md()
    section = md.split("### Q1.")[1].split("###")[0]
    assert "| 선택지 | 응답 수 | 비율 |\n" in section


def test_aggregate_tags_each_free_response_with_its_persona():
    md = _persona_md()
    free = md.split("## 자유 응답 전문")[1]
    assert "- [점포 관리자] 승인 단계가 없다" in free
    assert "- [본사 MD] 깔끔하다" in free
    assert "- [점포 관리자] 매장별 보기" in free


def test_english_persona_aggregate_has_no_korean_labels():
    md = _persona_md("en")
    for label in ("응답자 구성", "전체", "페르소나", "선택지"):
        assert label not in md
    assert "**Respondents**" in md and "| All |" in md


def test_aggregate_without_a_persona_question_is_not_split():
    from aipds.survey.models import SurveyResponse
    from aipds.survey.rollup import build_rollup
    from aipds.survey.store import _aggregate_markdown

    qn = _qn(questions=[Question(id="q1", text="유용?", type="scale"),
                        Question(id="q2", text="개선점", type="text")])
    responses = [SurveyResponse(response_id="1", submitted_at="t",
                                answers={"q1": 4, "q2": "좋다"})]
    md = _aggregate_markdown(qn, responses, build_rollup(qn.questions, responses, "now"),
                             "now", "ko")
    assert "응답자 구성" not in md and "페르소나" not in md
    assert "| 점수 | 응답 수 |" in md
    assert "- 좋다" in md
