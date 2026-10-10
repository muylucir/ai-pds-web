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
    """리포트는 aiplc-docs/**에 생성되는 산출물이므로 UI 언어가 아니라
    프로젝트 언어를 따른다."""
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
    """집계에는 수집한 데이터만 있다. Step 6의 판단 섹션은 룰의
    `validation-results.md`에 에이전트가 쓴다(proto/layout.SURVEY_AGGREGATE).

    빈 템플릿으로라도 여기 두면 에이전트가 이 파일을 채우고, 다음 취합이 그
    분석을 덮어쓴다 — 2026-10-06 tobacco에서 열린 설문 위에 에이전트 분석이
    붙어 있던 모양이 그것이다.
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
        # 에이전트가 읽는 안내: Step 6 종합이 어디로 가는지.
        assert "validation-results.md" in md, language


def test_aggregate_markdown_carries_the_survey_window():
    """검증 기간의 양 끝이 집계에 있어야 한다. 없으면 에이전트가 PM에게 설문
    시작 시각을 묻는다 — 2026-10-06 chicken의 확인 질문 Q3이 그것이었다."""
    from aipds.survey.store import _aggregate_markdown
    from aipds.survey.models import Rollup

    rollup = Rollup(count=0, per_question={}, rebuilt_at="2026-10-06T16:18:49+00:00")
    open_md = _aggregate_markdown(
        _qn(created_at="2026-10-06T16:16:07+00:00"), [], rollup,
        "2026-10-06T16:18:49+00:00", "ko")
    assert "**설문 시작 시각**: 2026-10-06T16:16:07+00:00" in open_md
    assert "설문 마감 시각" not in open_md  # 열린 설문에는 마감 시각이 없다

    closed_md = _aggregate_markdown(
        _qn(status="closed", created_at="2026-10-06T16:16:07+00:00",
            closed_at="2026-10-08T09:00:00+00:00"), [], rollup,
        "2026-10-08T09:01:00+00:00", "en")
    assert "**Survey started at**: 2026-10-06T16:16:07+00:00" in closed_md
    assert "**Survey closed at**: 2026-10-08T09:00:00+00:00" in closed_md


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
    """손상된 매니페스트가 임의 문자열을 실어 와도 리포트가 한국어로 나온다 —
    이 기능 이전 모든 프로젝트의 언어가 그것이다."""
    from aipds.survey.report_labels import labels
    assert labels("klingon") == labels("ko")
    assert labels("") == labels("ko")


# ---- 설문 집계는 슬러그별이고, 룰의 validation-results.md와 다른 파일이다 ----
#
# **슬러그별(2026-08-20 실측 test2222).** Path B는 프로토타입을 N개 만들고 취합
# 라우트는 슬러그별이다. 슬러그 없는 키였을 때는 셋을 취합하면 셋이 같은 키를
# 덮어써 마지막 것만 남았다 — 오류 없이 틀린 결과다.
#
# **룰의 파일과 분리(2026-10-06 실측 tobacco).** 집계를 `validation-results.md`에
# 쓰면 그 파일의 주인이 둘이 된다: 재취합이 에이전트의 Step 6 분석을 덮어쓰고,
# 리셋이 Part 2가 인용하는 근거를 지운다.

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
    """설문지 사본과 같은 디렉터리다. 갈라지면 삭제·아카이브 경로가 한쪽을
    잊는다 — `layout.artifact_dir`이 존재하는 이유가 그것이다."""
    from aipds.survey.store import questionnaire_md_key
    for slug in (_layout.SINGLE_ID, "flight-disruption-notice"):
        assert (aggregate_md_key(slug).rsplit("/", 1)[0]
                == questionnaire_md_key(slug).rsplit("/", 1)[0])


async def test_synthesis_never_touches_the_rules_results_file():
    """재취합이 에이전트의 Step 6 분석을 덮어쓰지 않는다 — 이 분리의 목적."""
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
    """실측 test2222의 모양이다 — 프로토타입 3개, 취합 3번."""
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
    # 각 파일이 자기 프로토타입의 설문 제목을 담아야 한다 — 덮어썼다면 셋이 같다.
    for slug in slugs:
        assert f"{slug} 검증" in project_s3.blobs[aggregate_md_key(slug)]


# ---- 페르소나로 나눈 집계 ----

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
    # 응답 0인 페르소나는 대상 사용자 중 누구에게 닿지 않았는지를 보여 준다.
    md = _persona_md()
    assert "- **응답자 구성**: 점포 관리자 3 · 본사 MD 1 · 해당 없음 / 기타 0" in md


def test_aggregate_splits_a_scale_question_by_persona():
    """전체 평균만으로는 '관리자 5점, 실제 사용자 2점'이 한 숫자로 뭉개진다."""
    md = _persona_md()
    section = md.split("### Q2.")[1].split("###")[0]
    assert "| 전체 | 4 | 3.25 | 1 | 0 | 2 | 1 | 0 |" in section
    assert "| 점포 관리자 | 3 | 2.67 | 0 | 0 | 2 | 1 | 0 |" in section
    assert "| 본사 MD | 1 | 5.0 | 1 | 0 | 0 | 0 | 0 |" in section
    # 응답이 없는 페르소나는 행을 만들지 않는다 — 헤더의 구성이 이미 보여 준다.
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
