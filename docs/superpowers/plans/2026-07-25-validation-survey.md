# 검증 설문 (문항 자동 생성 + 공개 폼 + S3 rollup 대시보드) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 프로토타입의 검증 가설·기능 목록에서 설문 문항을 자동 생성하고, 인증 없는 공개 링크로 응답을 받아, 프로토타입 탭에서 집계 대시보드로 보여준다.

**Architecture:** 응답은 S3에 객체-per-응답으로 쓰고(정본), 대시보드는 `rollup.json` 단일 객체만 읽는다(실측 500건 2.61s → 395ms). 문항 생성은 룰 프롬프트가 박힌 `StrandsDriver`를 재사용하지 않고 **일회성 Strands Agent**를 별도로 띄운다. 공개 라우트는 토큰만으로 동작하며 내부 식별자를 응답에 절대 담지 않는다. 스펙: `docs/superpowers/specs/2026-07-25-validation-survey-design.md`.

**Tech Stack:** Python 3.11 (FastAPI/Starlette, pydantic, boto3 via 기존 `S3Store`), strands-agents (BedrockModel, Opus 4.8), Next.js 15 App Router + Tailwind, pytest / Vitest+MSW.

## Global Constraints

- **모델 ID:** `os.environ["ANTHROPIC_MODEL"]` (기존 `agent/driver.py:93` 패턴 — 하드코딩 금지).
- **저장소는 S3만.** SQLite·DynamoDB 도입 금지 (스펙 §1: `userDataCausesReplacement: true`로 EBS가 교체되어 로컬 DB는 유실됨).
- **문항 타입 3종 고정:** `scale`(1–5 정수) · `choice`(단일 선택) · `text`(자유 응답). 다중 선택·분기·업로드 금지.
- **공개 응답 본문에 `project_id`/`slug`/집계를 절대 포함하지 않는다** (스펙 §5·§7).
- **응답 본문 상한:** 문항당 2000자, 전체 32KB 초과 → 413. 설문당 응답 1000건 초과 → 429.
- **rollup은 캐시, `responses/{uuid}.json`이 정본.** rollup 갱신 실패 시 응답은 204 성공(로그만).
- **text 응답 rollup 샘플 상한 20건** (rollup 무한 성장 방지). 전체 원문은 CSV로만.
- **에러 sanitize:** 에이전트/AWS 실패 상세는 서버 로그만, 응답에는 sanitize된 사유 (기존 `routes/prototypes.py` 패턴).
- **커밋 메시지 말미:** `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

## File Structure

```
backend/pathfinder/survey/
  __init__.py
  models.py        # pydantic: Question, Questionnaire, SurveyResponse, Rollup (Task 1)
  rollup.py        # 순수 집계 함수 — 응답 리스트 → Rollup (Task 2)
  store.py         # SurveyStore: S3 CRUD, 토큰 역인덱스, append, rollup, archive, CSV (Task 3-4)
  builder.py       # build_questionnaire: PROTOTYPE md → 에이전트 1턴 → Questionnaire (Task 5)
backend/pathfinder/routes/surveys.py   # 관리 4 + 공개 2 라우트 (Task 6-7)
backend/pathfinder/app.py              # survey_store_factory / questionnaire_agent_factory (Task 6)
frontend/lib/api/surveys.ts            # 관리 + 공개 API 클라이언트 (Task 8)
frontend/components/prototypes/SurveyPanel.tsx      # 생성·링크·마감·대시보드 (Task 9)
frontend/components/prototypes/SurveyDashboard.tsx  # 집계 렌더 (Task 9)
frontend/app/survey/[token]/page.tsx   # 공개 응답 폼 (Task 10)
frontend/components/survey/SurveyForm.tsx           # 3타입 문항 렌더 + 검증 (Task 10)
```

**Task 분할:** 1(모델) · 2(rollup 순수함수) · 3(store 기본 CRUD+토큰) · 4(store append/rollup/archive/CSV) · 5(builder) · 6(관리 라우트+app 배선) · 7(공개 라우트) · 8(프론트 API) · 9(패널+대시보드) · 10(공개 폼) · 11(문서/체크리스트).

순서: 1→2→3→4→5→6→7 순차(각자 앞 태스크 산출물 소비). 8은 7 이후. 9·10은 8 이후(서로 독립). 11 마지막.

---

### Task 1: pydantic 모델

**Files:**
- Create: `backend/pathfinder/survey/__init__.py` (빈 파일)
- Create: `backend/pathfinder/survey/models.py`
- Test: `backend/tests/test_survey_models.py`

**Interfaces:**
- Produces: `QuestionType = Literal["scale", "choice", "text"]`; `Question(id: str, text: str, type: QuestionType, options: list[str] = [], required: bool = True)`; `Questionnaire(token, status, slug, project_id, created_at, closed_at, title, hypothesis, questions)`; `SurveyResponse(response_id, submitted_at, answers: dict[str, str | int])`; `ScaleStat`, `ChoiceStat`, `TextStat`, `Rollup(count, rebuilt_at, per_question)`
- Consumes: 없음

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_survey_models.py`:

```python
import pytest
from pydantic import ValidationError
from pathfinder.survey.models import (Question, Questionnaire, SurveyResponse,
                                      Rollup, ScaleStat, ChoiceStat, TextStat)


def _q(**kw):
    base = {"id": "q1", "text": "유용했나요?", "type": "scale"}
    return Question(**{**base, **kw})


def test_choice_question_requires_options():
    # A choice question with no options is unanswerable -- reject at the model.
    with pytest.raises(ValidationError):
        Question(id="q1", text="어느 것?", type="choice", options=[])


def test_scale_and_text_reject_options():
    # scale is fixed 1-5 and text is free-form: options would be meaningless
    # and would silently render as a choice list in the public form.
    with pytest.raises(ValidationError):
        Question(id="q1", text="t", type="scale", options=["1", "2"])
    with pytest.raises(ValidationError):
        Question(id="q2", text="t", type="text", options=["a"])


def test_choice_question_accepts_options():
    q = Question(id="q1", text="어느 것?", type="choice", options=["A", "B"])
    assert q.options == ["A", "B"]
    assert q.required is True


def test_questionnaire_roundtrips_json():
    qn = Questionnaire(
        token="t" * 43, status="open", slug="demo", project_id="p1",
        created_at="2026-07-25T00:00:00Z", closed_at=None,
        title="검증 설문", hypothesis="가설", questions=[_q()])
    again = Questionnaire.model_validate_json(qn.model_dump_json())
    assert again.token == "t" * 43
    assert again.questions[0].text == "유용했나요?"


def test_questionnaire_rejects_duplicate_question_ids():
    # Duplicate ids would make the answers dict lose one question's response.
    with pytest.raises(ValidationError):
        Questionnaire(
            token="t", status="open", slug="s", project_id="p",
            created_at="x", closed_at=None, title="t", hypothesis="h",
            questions=[_q(id="q1"), _q(id="q1")])


def test_questionnaire_rejects_empty_questions():
    with pytest.raises(ValidationError):
        Questionnaire(token="t", status="open", slug="s", project_id="p",
                      created_at="x", closed_at=None, title="t",
                      hypothesis="h", questions=[])


def test_response_model():
    r = SurveyResponse(response_id="abc", submitted_at="2026-07-25T00:00:00Z",
                       answers={"q1": 4, "q2": "매우 유용"})
    assert r.answers["q1"] == 4


def test_rollup_model():
    ru = Rollup(count=2, rebuilt_at="2026-07-25T00:00:00Z", per_question={
        "q1": ScaleStat(n=2, mean=4.5, distribution={"1": 0, "2": 0, "3": 0,
                                                     "4": 1, "5": 1}),
        "q2": ChoiceStat(n=2, counts={"A": 2}),
        "q3": TextStat(n=1, samples=["좋았다"]),
    })
    assert ru.per_question["q1"].type == "scale"
    assert ru.per_question["q2"].type == "choice"
    assert ru.per_question["q3"].type == "text"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_survey_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pathfinder.survey'`

- [ ] **Step 3: 모델 구현**

`backend/pathfinder/survey/__init__.py`: 빈 파일 생성.

`backend/pathfinder/survey/models.py`:

```python
# backend/pathfinder/survey/models.py — validation-survey wire/storage models.
from __future__ import annotations
from typing import Literal, Union
from pydantic import BaseModel, Field, model_validator

QuestionType = Literal["scale", "choice", "text"]
SurveyStatus = Literal["open", "closed"]

#: scale is a fixed 1-5 integer range; the public form renders it as such.
SCALE_MIN = 1
SCALE_MAX = 5


class Question(BaseModel):
    id: str
    text: str
    type: QuestionType
    options: list[str] = Field(default_factory=list)
    required: bool = True

    @model_validator(mode="after")
    def _options_match_type(self) -> "Question":
        if self.type == "choice" and len(self.options) < 2:
            raise ValueError("choice question needs at least 2 options")
        if self.type in ("scale", "text") and self.options:
            raise ValueError(f"{self.type} question must not carry options")
        return self


class Questionnaire(BaseModel):
    token: str
    status: SurveyStatus
    slug: str
    project_id: str
    created_at: str
    closed_at: str | None = None
    title: str
    hypothesis: str
    questions: list[Question]

    @model_validator(mode="after")
    def _questions_sane(self) -> "Questionnaire":
        if not self.questions:
            raise ValueError("questionnaire needs at least one question")
        ids = [q.id for q in self.questions]
        if len(set(ids)) != len(ids):
            # Duplicate ids collapse in the answers dict, silently dropping a
            # question's responses.
            raise ValueError("question ids must be unique")
        return self


class SurveyResponse(BaseModel):
    response_id: str
    submitted_at: str
    answers: dict[str, Union[str, int]]


class ScaleStat(BaseModel):
    type: Literal["scale"] = "scale"
    n: int
    mean: float
    distribution: dict[str, int]


class ChoiceStat(BaseModel):
    type: Literal["choice"] = "choice"
    n: int
    counts: dict[str, int]


class TextStat(BaseModel):
    type: Literal["text"] = "text"
    n: int
    samples: list[str]


Stat = Union[ScaleStat, ChoiceStat, TextStat]


class Rollup(BaseModel):
    count: int
    rebuilt_at: str
    per_question: dict[str, Stat]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_survey_models.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: 커밋**

```bash
git add backend/pathfinder/survey/ backend/tests/test_survey_models.py
git commit -m "feat(survey): questionnaire/response/rollup models with type-option validation

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: rollup 집계 순수 함수

**Files:**
- Create: `backend/pathfinder/survey/rollup.py`
- Test: `backend/tests/test_survey_rollup.py`

**Interfaces:**
- Produces: `TEXT_SAMPLE_LIMIT = 20`; `build_rollup(questions: list[Question], responses: list[SurveyResponse], now: str) -> Rollup`
- Consumes: Task 1의 `Question`, `SurveyResponse`, `Rollup`, `ScaleStat`, `ChoiceStat`, `TextStat`

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_survey_rollup.py`:

```python
from pathfinder.survey.models import Question, SurveyResponse
from pathfinder.survey.rollup import build_rollup, TEXT_SAMPLE_LIMIT

NOW = "2026-07-25T00:00:00Z"

QUESTIONS = [
    Question(id="q1", text="유용?", type="scale"),
    Question(id="q2", text="어느 것?", type="choice", options=["A", "B"]),
    Question(id="q3", text="자유", type="text", required=False),
]


def _r(rid, answers):
    return SurveyResponse(response_id=rid, submitted_at=NOW, answers=answers)


def test_scale_mean_and_distribution():
    responses = [_r("1", {"q1": 4}), _r("2", {"q1": 5}), _r("3", {"q1": 4})]
    ru = build_rollup(QUESTIONS, responses, NOW)
    stat = ru.per_question["q1"]
    assert stat.n == 3
    assert stat.mean == round((4 + 5 + 4) / 3, 2)
    assert stat.distribution == {"1": 0, "2": 0, "3": 0, "4": 2, "5": 1}
    assert ru.count == 3


def test_choice_counts_include_zero_options():
    # An option nobody picked must still appear as 0 -- otherwise the dashboard
    # silently hides the fact that it was offered.
    responses = [_r("1", {"q2": "A"}), _r("2", {"q2": "A"})]
    stat = build_rollup(QUESTIONS, responses, NOW).per_question["q2"]
    assert stat.counts == {"A": 2, "B": 0}
    assert stat.n == 2


def test_text_samples_capped_and_blank_skipped():
    responses = [_r(str(i), {"q3": f"의견 {i}"}) for i in range(30)]
    responses.append(_r("blank", {"q3": "   "}))
    stat = build_rollup(QUESTIONS, responses, NOW).per_question["q3"]
    assert stat.n == 30              # blank not counted
    assert len(stat.samples) == TEXT_SAMPLE_LIMIT


def test_missing_optional_answers_are_not_counted():
    responses = [_r("1", {"q1": 3}), _r("2", {})]
    ru = build_rollup(QUESTIONS, responses, NOW)
    assert ru.count == 2             # both responses exist
    assert ru.per_question["q1"].n == 1
    assert ru.per_question["q3"].n == 0


def test_out_of_range_and_non_numeric_scale_ignored():
    # A hand-crafted POST could carry 9 or "abc"; the aggregate must not skew
    # or crash on it.
    responses = [_r("1", {"q1": 4}), _r("2", {"q1": 9}), _r("3", {"q1": "abc"})]
    stat = build_rollup(QUESTIONS, responses, NOW).per_question["q1"]
    assert stat.n == 1
    assert stat.mean == 4.0


def test_unknown_answer_keys_ignored():
    responses = [_r("1", {"q1": 4, "qZ": "몰래"})]
    ru = build_rollup(QUESTIONS, responses, NOW)
    assert set(ru.per_question) == {"q1", "q2", "q3"}


def test_empty_responses_yield_zeroed_stats():
    ru = build_rollup(QUESTIONS, [], NOW)
    assert ru.count == 0
    assert ru.per_question["q1"].n == 0 and ru.per_question["q1"].mean == 0.0
    assert ru.per_question["q2"].counts == {"A": 0, "B": 0}
    assert ru.per_question["q3"].samples == []
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_survey_rollup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pathfinder.survey.rollup'`

- [ ] **Step 3: 구현**

`backend/pathfinder/survey/rollup.py`:

```python
# backend/pathfinder/survey/rollup.py — pure aggregation: responses -> Rollup.
#
# Kept free of S3/HTTP so the dashboard's numbers are unit-testable, and so a
# rollup rebuild is a pure re-derivation from the response objects (which are
# the source of truth -- the stored rollup is only a cache).
from __future__ import annotations

from pathfinder.survey.models import (ChoiceStat, Question, Rollup, ScaleStat,
                                      SCALE_MAX, SCALE_MIN, SurveyResponse,
                                      TextStat)

#: Cap on text answers kept in the rollup. The full text lives in the response
#: objects and is exported via CSV; an uncapped rollup would grow without bound.
TEXT_SAMPLE_LIMIT = 20


def _scale_stat(values: list[object]) -> ScaleStat:
    dist = {str(i): 0 for i in range(SCALE_MIN, SCALE_MAX + 1)}
    nums: list[int] = []
    for v in values:
        # bool is an int subclass but is never a valid scale answer.
        if isinstance(v, bool) or not isinstance(v, int):
            continue
        if SCALE_MIN <= v <= SCALE_MAX:
            nums.append(v)
            dist[str(v)] += 1
    mean = round(sum(nums) / len(nums), 2) if nums else 0.0
    return ScaleStat(n=len(nums), mean=mean, distribution=dist)


def _choice_stat(values: list[object], options: list[str]) -> ChoiceStat:
    # Seed every offered option at 0 so the dashboard shows unchosen options.
    counts = {opt: 0 for opt in options}
    n = 0
    for v in values:
        if isinstance(v, str) and v in counts:
            counts[v] += 1
            n += 1
    return ChoiceStat(n=n, counts=counts)


def _text_stat(values: list[object]) -> TextStat:
    texts = [v.strip() for v in values if isinstance(v, str) and v.strip()]
    return TextStat(n=len(texts), samples=texts[:TEXT_SAMPLE_LIMIT])


def build_rollup(questions: list[Question], responses: list[SurveyResponse],
                 now: str) -> Rollup:
    per_question: dict = {}
    for q in questions:
        values = [r.answers[q.id] for r in responses if q.id in r.answers]
        if q.type == "scale":
            per_question[q.id] = _scale_stat(values)
        elif q.type == "choice":
            per_question[q.id] = _choice_stat(values, q.options)
        else:
            per_question[q.id] = _text_stat(values)
    return Rollup(count=len(responses), rebuilt_at=now,
                  per_question=per_question)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_survey_rollup.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: 커밋**

```bash
git add backend/pathfinder/survey/rollup.py backend/tests/test_survey_rollup.py
git commit -m "feat(survey): pure rollup aggregation with scale/choice/text stats

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: SurveyStore — questionnaire CRUD + 토큰 역인덱스

**Files:**
- Create: `backend/pathfinder/survey/store.py`
- Test: `backend/tests/test_survey_store.py`

**Interfaces:**
- Produces:

```python
class SurveyStore:
    def __init__(self, project_s3, root_s3, slug: str, project_id: str): ...
    # project_s3: 프로젝트 프리픽스 스토어(projects/{pid}/), root_s3: 버킷 루트 스토어
    async def save_questionnaire(self, qn: Questionnaire) -> None   # + by-token 역인덱스 + md 사본
    async def load_questionnaire(self) -> Questionnaire             # FileNotFoundError
    async def close(self) -> Questionnaire                          # 멱등, closed_at 기록
    @staticmethod
    async def resolve_token(root_s3, token: str) -> tuple[str, str] # (project_id, slug), FileNotFoundError
    def public_url_path(token: str) -> str                          # "/survey/{token}"
```

- Produces (모듈 상수): `SURVEY_PREFIX = "prototypes/{slug}/survey/"` 조립 헬퍼, `QUESTIONNAIRE_MD_KEY` (= `aiplc-docs/discovery/prototypes/{slug}/validation-questionnaire.md`)
- Consumes: Task 1 모델, `pathfinder.s3store.S3StoreLike`(get/put/list/delete_prefix), 테스트는 `tests/fakes/in_memory_s3.py`의 `FakeS3Store`
- 토큰 생성은 store가 하지 않는다 — 호출자(builder/route)가 `secrets.token_urlsafe(32)`로 만들어 `Questionnaire.token`에 담아 넘긴다(테스트 결정성).

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_survey_store.py`:

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_survey_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pathfinder.survey.store'`

- [ ] **Step 3: 구현**

`backend/pathfinder/survey/store.py`:

```python
# backend/pathfinder/survey/store.py — S3 persistence for validation surveys.
#
# Layout (spec §3), under the project prefix:
#   prototypes/{slug}/survey/questionnaire.json   definition + token + status
#   prototypes/{slug}/survey/rollup.json          aggregate CACHE
#   prototypes/{slug}/survey/responses/{uuid}.json  one response = one object (SOURCE OF TRUTH)
#   prototypes/{slug}/survey/archive/{closed_at}/   previous survey on regeneration
# and at the bucket root (needed before we know which project a token belongs to):
#   surveys/by-token/{token}.json                 {"project_id":..., "slug":...}
from __future__ import annotations

import json
import logging

from pathfinder.survey.models import Questionnaire

_log = logging.getLogger(__name__)

TOKEN_INDEX_PREFIX = "surveys/by-token/"


def survey_prefix(slug: str) -> str:
    return f"prototypes/{slug}/survey/"


def questionnaire_key(slug: str) -> str:
    return f"{survey_prefix(slug)}questionnaire.json"


def rollup_key(slug: str) -> str:
    return f"{survey_prefix(slug)}rollup.json"


def responses_prefix(slug: str) -> str:
    return f"{survey_prefix(slug)}responses/"


def archive_prefix(slug: str, closed_at: str) -> str:
    return f"{survey_prefix(slug)}archive/{closed_at}/"


def questionnaire_md_key(slug: str) -> str:
    # aiplc-docs/ so the existing artifacts viewer can serve it (that route is
    # hard-limited to this subtree).
    return f"aiplc-docs/discovery/prototypes/{slug}/validation-questionnaire.md"


def _to_markdown(qn: Questionnaire) -> str:
    lines = [f"# {qn.title}", "", f"**검증 가설**: {qn.hypothesis}", ""]
    for i, q in enumerate(qn.questions, start=1):
        suffix = "" if q.required else " (선택)"
        lines.append(f"## Question {i}{suffix}")
        lines.append(q.text)
        lines.append("")
        if q.type == "scale":
            lines.append("1(전혀 아니다) ~ 5(매우 그렇다) 중 선택")
        elif q.type == "choice":
            lines.extend(f"- {opt}" for opt in q.options)
        else:
            lines.append("(자유 응답)")
        lines.append("")
    return "\n".join(lines)


class SurveyStore:
    """One prototype's survey. `project_s3` is the project-prefixed store
    (projects/{pid}/); `root_s3` is a bucket-root store used only for the
    token index, which must be readable before the project is known."""

    def __init__(self, project_s3, root_s3, slug: str, project_id: str):
        self._s3 = project_s3
        self._root = root_s3
        self.slug = slug
        self.project_id = project_id

    @staticmethod
    def public_url_path(token: str) -> str:
        return f"/survey/{token}"

    @staticmethod
    async def resolve_token(root_s3, token: str) -> tuple[str, str]:
        raw = await root_s3.get(f"{TOKEN_INDEX_PREFIX}{token}.json")
        data = json.loads(raw)
        return data["project_id"], data["slug"]

    async def save_questionnaire(self, qn: Questionnaire) -> None:
        await self._s3.put(questionnaire_key(self.slug), qn.model_dump_json())
        await self._root.put(
            f"{TOKEN_INDEX_PREFIX}{qn.token}.json",
            json.dumps({"project_id": qn.project_id, "slug": qn.slug}))
        await self._s3.put(questionnaire_md_key(self.slug), _to_markdown(qn))

    async def load_questionnaire(self) -> Questionnaire:
        raw = await self._s3.get(questionnaire_key(self.slug))
        return Questionnaire.model_validate_json(raw)

    async def close(self, now: str | None = None) -> Questionnaire:
        qn = await self.load_questionnaire()
        if qn.status == "closed":
            return qn  # idempotent: never bump closed_at
        from datetime import datetime, timezone
        stamp = now or datetime.now(timezone.utc).isoformat()
        closed = qn.model_copy(update={"status": "closed", "closed_at": stamp})
        await self._s3.put(questionnaire_key(self.slug), closed.model_dump_json())
        return closed
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_survey_store.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: 커밋**

```bash
git add backend/pathfinder/survey/store.py backend/tests/test_survey_store.py
git commit -m "feat(survey): SurveyStore questionnaire CRUD, token index, md copy

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: SurveyStore — 응답 append / rollup / archive / CSV

**Files:**
- Modify: `backend/pathfinder/survey/store.py`
- Test: `backend/tests/test_survey_store_responses.py`

**Interfaces:**
- Produces (SurveyStore 메서드 추가):

```python
    async def append_response(self, resp: SurveyResponse) -> None   # responses/{id}.json PUT (정본)
    async def response_count(self) -> int                           # list(responses/) 길이
    async def load_responses(self) -> list[SurveyResponse]          # 병렬 get (집계 재구축용)
    async def refresh_rollup(self, now: str | None = None) -> Rollup  # 재구축 + 저장
    async def get_rollup(self, now: str | None = None) -> Rollup     # 캐시 읽기, count 불일치/부재 시 재구축
    async def archive_current(self) -> None                          # 마감된 설문·응답·rollup을 archive/로 이관
    async def responses_csv(self) -> str                             # 문항 헤더 + 응답 행
```

- Consumes: Task 1 모델, Task 2 `build_rollup`, Task 3의 키 헬퍼
- **불일치 감지 계약**: `get_rollup`은 저장된 rollup의 `count`와 `response_count()`가 다르면 재구축한다 — rollup은 캐시이므로 수치가 틀리는 것보다 한 번 느린 게 낫다.
- **병렬 get 필수**: `load_responses`는 `asyncio.gather` 사용 (순차 get은 건당 61ms — 스펙 §2).

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_survey_store_responses.py`:

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_survey_store_responses.py -v`
Expected: FAIL — `AttributeError: 'SurveyStore' object has no attribute 'append_response'`

- [ ] **Step 3: 구현 — store.py에 추가**

`backend/pathfinder/survey/store.py` 상단 import에 추가:

```python
import asyncio
import csv
import io
from datetime import datetime, timezone

from pathfinder.survey.models import Questionnaire, Rollup, SurveyResponse
from pathfinder.survey.rollup import build_rollup
```

`SurveyStore` 클래스에 메서드 추가:

```python
    # ---- responses (source of truth) ----

    async def append_response(self, resp: SurveyResponse) -> None:
        key = f"{responses_prefix(self.slug)}{resp.response_id}.json"
        await self._s3.put(key, resp.model_dump_json())

    async def response_count(self) -> int:
        return len(await self._s3.list(responses_prefix(self.slug)))

    async def load_responses(self) -> list[SurveyResponse]:
        keys = await self._s3.list(responses_prefix(self.slug))
        # Parallel, never sequential: measured 61ms per object round-trip, so a
        # sequential rebuild of 500 responses would take ~30s (spec §2).
        raw = await asyncio.gather(*[self._s3.get(k) for k in keys])
        return [SurveyResponse.model_validate_json(r) for r in raw]

    # ---- rollup (cache) ----

    @staticmethod
    def _now(now: str | None) -> str:
        return now or datetime.now(timezone.utc).isoformat()

    async def refresh_rollup(self, now: str | None = None) -> Rollup:
        qn = await self.load_questionnaire()
        responses = await self.load_responses()
        ru = build_rollup(qn.questions, responses, self._now(now))
        await self._s3.put(rollup_key(self.slug), ru.model_dump_json())
        return ru

    async def get_rollup(self, now: str | None = None) -> Rollup:
        count = await self.response_count()
        try:
            cached = Rollup.model_validate_json(
                await self._s3.get(rollup_key(self.slug)))
        except (FileNotFoundError, ValueError):
            cached = None
        if cached is not None and cached.count == count:
            return cached
        # Absent, unparseable, or stale (a rollup write can fail after the
        # response PUT succeeded -- the response is still committed). Rebuild
        # from the source of truth rather than report wrong numbers.
        return await self.refresh_rollup(now)

    # ---- archive on regeneration ----

    async def archive_current(self) -> None:
        """Move the closed survey's definition, responses and rollup under
        archive/{closed_at}/. Reusing responses/ across surveys would mix
        answers to OLD questions into the new survey's aggregate and CSV --
        silently wrong numbers."""
        qn = await self.load_questionnaire()
        if qn.status != "closed" or not qn.closed_at:
            raise ValueError("only a closed survey can be archived")
        dest = archive_prefix(self.slug, qn.closed_at)

        await self._s3.put(f"{dest}questionnaire.json", qn.model_dump_json())
        for key in await self._s3.list(responses_prefix(self.slug)):
            body = await self._s3.get(key)
            name = key.rsplit("/", 1)[-1]
            await self._s3.put(f"{dest}responses/{name}", body)
        try:
            await self._s3.put(f"{dest}rollup.json",
                               await self._s3.get(rollup_key(self.slug)))
        except FileNotFoundError:
            pass  # never aggregated; nothing to preserve
        await self._s3.delete_prefix(responses_prefix(self.slug))
        await self._s3.delete_prefix(rollup_key(self.slug))

    # ---- export ----

    async def responses_csv(self) -> str:
        qn = await self.load_questionnaire()
        responses = await self.load_responses()
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["response_id", "submitted_at"] +
                        [q.text for q in qn.questions])
        for r in sorted(responses, key=lambda x: x.submitted_at):
            writer.writerow([r.response_id, r.submitted_at] +
                            [r.answers.get(q.id, "") for q in qn.questions])
        return buf.getvalue()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_survey_store_responses.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: 전체 스위트 회귀 확인**

Run: `cd backend && .venv/bin/pytest`
Expected: 기존 테스트 전부 green + 신규

- [ ] **Step 6: 커밋**

```bash
git add backend/pathfinder/survey/store.py backend/tests/test_survey_store_responses.py
git commit -m "feat(survey): response append, rollup cache with staleness rebuild, archive, CSV

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: builder — PROTOTYPE md → 에이전트 1턴 → Questionnaire

**Files:**
- Create: `backend/pathfinder/survey/builder.py`
- Test: `backend/tests/test_survey_builder.py`

**Interfaces:**
- Produces:

```python
QUESTIONNAIRE_PROMPT: str   # 룰 관점(기능별 시그널·pain point 매핑)을 담은 지시문 템플릿
def build_prompt(prototype_md: str) -> str
async def build_questionnaire(prototype_md: str, agent, *, token: str,
                              project_id: str, slug: str, now: str,
                              attempts: int = 2) -> Questionnaire
```

- `agent`는 `async def __call__(prompt: str) -> str` 인터페이스(반환은 모델 텍스트). 테스트는 스크립트된 fake를 주입한다. 실제 구현은 Task 6의 `questionnaire_agent_factory`가 Strands `Agent`를 감싼 얇은 콜러블을 만든다.
- **재시도 계약**: JSON 파싱/스키마 위반 시 총 `attempts`회 시도, 마지막 실패는 `ValueError` — 라우트가 502로 변환.
- **PROTOTYPE md 헤딩에 의존하지 않는다**: 실제 `PROTOTYPE-*.md`의 헤딩은 검증 룰 템플릿과 다르다(`## Use Case Overview`/`### Success Criteria` 등). md 전문을 프롬프트에 넣고 모델이 가설·기능을 뽑게 한다 — 헤딩 파싱은 취약.
- Consumes: Task 1 모델

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_survey_builder.py`:

```python
import json
import pytest
from pathfinder.survey.builder import build_prompt, build_questionnaire

MD = """# PROTOTYPE-demo
## Use Case Overview
### Success Criteria
- NOTAM 판독 시간 50% 단축
## Tools
### Tool 1: summarize
"""

VALID = {
    "title": "NOTAM 프로토타입 검증",
    "hypothesis": "판독 시간을 절반으로 줄인다",
    "questions": [
        {"id": "q1", "text": "요약이 정확했나요?", "type": "scale", "required": True},
        {"id": "q2", "text": "가장 유용한 기능은?", "type": "choice",
         "options": ["요약", "검색"], "required": True},
        {"id": "q3", "text": "개선점", "type": "text", "required": False},
    ],
}


class FakeAgent:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    async def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0)


def test_prompt_embeds_md_and_type_constraint():
    p = build_prompt(MD)
    assert "NOTAM 판독 시간 50% 단축" in p     # full md is handed to the model
    assert "scale" in p and "choice" in p and "text" in p
    assert "JSON" in p


async def test_builds_questionnaire_from_valid_json():
    agent = FakeAgent(json.dumps(VALID, ensure_ascii=False))
    qn = await build_questionnaire(MD, agent, token="tok", project_id="p1",
                                   slug="demo", now="2026-07-25T00:00:00Z")
    assert qn.token == "tok" and qn.project_id == "p1" and qn.slug == "demo"
    assert qn.status == "open" and qn.closed_at is None
    assert [q.id for q in qn.questions] == ["q1", "q2", "q3"]
    assert len(agent.prompts) == 1


async def test_tolerates_fenced_json():
    # Models routinely wrap JSON in ```json fences despite instructions.
    agent = FakeAgent("```json\n" + json.dumps(VALID) + "\n```")
    qn = await build_questionnaire(MD, agent, token="t", project_id="p",
                                   slug="s", now="n")
    assert len(qn.questions) == 3


async def test_retries_once_on_unparseable_reply():
    agent = FakeAgent("설문을 만들었습니다!", json.dumps(VALID))
    qn = await build_questionnaire(MD, agent, token="t", project_id="p",
                                   slug="s", now="n")
    assert len(qn.questions) == 3
    assert len(agent.prompts) == 2


async def test_retries_once_on_schema_violation():
    bad = {**VALID, "questions": [
        {"id": "q1", "text": "t", "type": "choice", "options": []}]}
    agent = FakeAgent(json.dumps(bad), json.dumps(VALID))
    qn = await build_questionnaire(MD, agent, token="t", project_id="p",
                                   slug="s", now="n")
    assert len(qn.questions) == 3


async def test_raises_after_exhausting_attempts():
    agent = FakeAgent("nope", "still nope")
    with pytest.raises(ValueError):
        await build_questionnaire(MD, agent, token="t", project_id="p",
                                  slug="s", now="n")
    assert len(agent.prompts) == 2
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_survey_builder.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pathfinder.survey.builder'`

- [ ] **Step 3: 구현**

`backend/pathfinder/survey/builder.py`:

```python
# backend/pathfinder/survey/builder.py — PROTOTYPE spec -> survey questions.
#
# One agent turn, not the Discovery StrandsDriver: that driver bakes in the
# AIPLC rules system prompt, the workspace tool set and a session manager, none
# of which belong in a stateless "turn this spec into questions" call.
from __future__ import annotations

import json
import logging
import re

from pathfinder.survey.models import Questionnaire

_log = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

# The validation rule judges a prototype by feature-level signal and pain-point
# mapping (prototype-validation.md Step 6), so the questions must produce that
# evidence -- otherwise the PM gets answers they cannot synthesise.
QUESTIONNAIRE_PROMPT = """\
아래는 프로토타입 명세(PROTOTYPE-*.md)다. 이 프로토타입을 실제로 사용해 본
최종 사용자에게 물을 검증 설문 문항을 만들어라.

요구사항:
- 문항 6~10개.
- 명세의 검증 가설·성공 기준이 참인지 판단할 근거를 얻는 문항을 포함한다.
- 각 주요 기능이 사용자의 문제를 실제로 해결했는지 묻는 문항을 포함한다.
- 개선점·누락된 요구를 드러내는 자유 응답 문항을 최소 1개 포함한다.
- 유도 질문(원하는 답을 암시하는 질문)을 쓰지 않는다.

문항 타입은 정확히 다음 3종만 사용한다:
- "scale": 1~5 척도. options를 넣지 않는다.
- "choice": 단일 선택. options에 2개 이상의 선택지를 넣는다.
- "text": 자유 응답. options를 넣지 않는다.

출력은 아래 형태의 JSON **하나만** 출력한다(설명·머리말·코드펜스 금지):
{{"title": "...", "hypothesis": "...", "questions": [
  {{"id": "q1", "text": "...", "type": "scale", "required": true}},
  {{"id": "q2", "text": "...", "type": "choice", "options": ["...", "..."], "required": true}},
  {{"id": "q3", "text": "...", "type": "text", "required": false}}
]}}

명세:
---
{md}
---
"""


def build_prompt(prototype_md: str) -> str:
    return QUESTIONNAIRE_PROMPT.format(md=prototype_md)


def _extract_json(reply: str) -> dict:
    fenced = _FENCE_RE.search(reply)
    candidate = fenced.group(1) if fenced else reply
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in reply")
    return json.loads(candidate[start:end + 1])


async def build_questionnaire(prototype_md: str, agent, *, token: str,
                              project_id: str, slug: str, now: str,
                              attempts: int = 2) -> Questionnaire:
    prompt = build_prompt(prototype_md)
    last_error: Exception | None = None
    for attempt in range(attempts):
        reply = await agent(prompt)
        try:
            data = _extract_json(reply)
            return Questionnaire(
                token=token, status="open", slug=slug, project_id=project_id,
                created_at=now, closed_at=None,
                title=data["title"], hypothesis=data["hypothesis"],
                questions=data["questions"])
        except Exception as exc:  # noqa: BLE001 — retry on any malformed reply
            last_error = exc
            _log.warning("questionnaire generation attempt %d failed: %s",
                         attempt + 1, exc)
    raise ValueError(f"questionnaire generation failed: {last_error}")
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_survey_builder.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: 커밋**

```bash
git add backend/pathfinder/survey/builder.py backend/tests/test_survey_builder.py
git commit -m "feat(survey): questionnaire builder — one agent turn, fenced-JSON tolerant, retries once

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: 관리 라우트 + app.py 배선

**Files:**
- Create: `backend/pathfinder/routes/surveys.py`
- Modify: `backend/pathfinder/app.py` (factories + router include)
- Test: `backend/tests/test_routes_surveys.py`

**Interfaces:**
- Produces (라우트):
  - `POST /projects/{pid}/prototypes/{slug}/survey` → 201 `{token, url, questions}`. 열린 설문 존재 시 409. PROTOTYPE md 없음 404. 생성 실패 502(sanitized). 마감된 설문 존재 시 `archive_current()` 후 새로 생성
  - `GET /projects/{pid}/prototypes/{slug}/survey` → `{questionnaire, rollup}`. 없으면 404
  - `POST /projects/{pid}/prototypes/{slug}/survey/close` → 204, 멱등
  - `GET /projects/{pid}/prototypes/{slug}/survey/responses.csv` → `text/csv` + Content-Disposition
- Produces (app.py, monkeypatchable — 기존 `s3_store_factory` 패턴):
  - `def survey_store_factory(project_id: str, slug: str) -> SurveyStore`
  - `def questionnaire_agent_factory()` → `async (prompt) -> str` 콜러블 (Strands Agent 1회 호출; `ANTHROPIC_MODEL` 사용)
  - `def surveys_root_s3_factory() -> S3StoreLike` (버킷 루트 = prefix `""`)
- Consumes: Task 3·4 `SurveyStore`, Task 5 `build_questionnaire`, 기존 `registry.is_registered`, `s3_store_factory`
- 토큰은 라우트에서 `secrets.token_urlsafe(32)`로 생성해 builder에 넘긴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_routes_surveys.py`:

```python
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
    asyncio.get_event_loop().run_until_complete(store.append_response(
        SurveyResponse(response_id="r1", submitted_at="2026-07-25T00:00:00Z",
                       answers={"q1": 5, "q2": "좋음"})))
    resp = client.get(f"/projects/{PID}/prototypes/{SLUG}/survey/responses.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "유용?" in resp.text and "좋음" in resp.text


def test_unknown_project_404(env):
    assert client.post("/projects/nope/prototypes/x/survey").status_code == 404
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_routes_surveys.py -v`
Expected: FAIL — 404 (라우트 미등록) / `AttributeError: module 'pathfinder.app' has no attribute 'survey_store_factory'`

- [ ] **Step 3: 라우트 구현**

`backend/pathfinder/routes/surveys.py`:

```python
# backend/pathfinder/routes/surveys.py — validation survey: admin routes.
#
# The public (token-only) routes live in the same module but are registered
# without the /projects prefix -- see routes/surveys_public.py in Task 7.
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from starlette.responses import Response

from pathfinder.survey.builder import build_questionnaire
from pathfinder.survey.store import SurveyStore

_log = logging.getLogger(__name__)

router = APIRouter()

TOKEN_BYTES = 32


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_registered(pid: str) -> None:
    import pathfinder.app as app_module
    if not app_module.registry.is_registered(pid):
        raise HTTPException(status_code=404, detail="unknown project")


def _store(pid: str, slug: str) -> SurveyStore:
    import pathfinder.app as app_module
    return app_module.survey_store_factory(pid, slug)


@router.post("/projects/{pid}/prototypes/{slug}/survey", status_code=201)
async def create_survey(pid: str, slug: str):
    import pathfinder.app as app_module
    _require_registered(pid)
    store = _store(pid, slug)

    try:
        existing = await store.load_questionnaire()
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if existing.status == "open":
            raise HTTPException(status_code=409, detail="survey already open")
        # Closed: archive it so old answers never mix into the new survey's
        # aggregate or CSV (spec §3).
        await store.archive_current()

    s3 = app_module.s3_store_factory(pid)
    spec_key = f"aiplc-docs/discovery/prototypes/{slug}/PROTOTYPE-{slug}.md"
    try:
        prototype_md = await s3.get(spec_key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="prototype spec not found")

    token = secrets.token_urlsafe(TOKEN_BYTES)
    try:
        qn = await build_questionnaire(
            prototype_md, app_module.questionnaire_agent_factory(),
            token=token, project_id=pid, slug=slug, now=_now())
    except Exception:
        # Model/AWS detail can carry credentials -- log it, return a sanitized
        # reason (same policy as routes/prototypes.py).
        _log.exception("questionnaire generation failed: %s/%s", pid, slug)
        raise HTTPException(status_code=502,
                            detail="questionnaire generation failed")

    await store.save_questionnaire(qn)
    return {"token": qn.token, "url": SurveyStore.public_url_path(qn.token),
            "questions": [q.model_dump() for q in qn.questions]}


@router.get("/projects/{pid}/prototypes/{slug}/survey")
async def get_survey(pid: str, slug: str):
    _require_registered(pid)
    store = _store(pid, slug)
    try:
        qn = await store.load_questionnaire()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="no survey")
    rollup = await store.get_rollup()
    return {"questionnaire": qn.model_dump(),
            "url": SurveyStore.public_url_path(qn.token),
            "rollup": rollup.model_dump()}


@router.post("/projects/{pid}/prototypes/{slug}/survey/close", status_code=204)
async def close_survey(pid: str, slug: str):
    _require_registered(pid)
    store = _store(pid, slug)
    try:
        await store.close()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="no survey")
    return Response(status_code=204)


@router.get("/projects/{pid}/prototypes/{slug}/survey/responses.csv")
async def export_csv(pid: str, slug: str):
    _require_registered(pid)
    store = _store(pid, slug)
    try:
        csv_text = await store.responses_csv()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="no survey")
    filename = f"survey-{slug}.csv"
    return Response(content=csv_text, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             f'attachment; filename="{filename}"'})
```

- [ ] **Step 4: app.py 배선**

`backend/pathfinder/app.py` — `proto_session_factory` 아래에 추가:

```python
# ---- validation survey wiring (routes/surveys.py) ----


def surveys_root_s3_factory() -> S3StoreLike:
    """Bucket-root store: the token index must be readable before we know
    which project a token belongs to."""
    region = os.environ.get("PATHFINDER_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("PATHFINDER_S3_BUCKET", "")
    client = boto3.client("s3", region_name=region)
    return S3Store(bucket=bucket, prefix="", client=client)


def survey_store_factory(project_id: str, slug: str):
    from pathfinder.survey.store import SurveyStore
    return SurveyStore(s3_store_factory(project_id), surveys_root_s3_factory(),
                       slug=slug, project_id=project_id)


def questionnaire_agent_factory():
    """A one-shot `async (prompt) -> str` callable. Deliberately NOT
    StrandsDriver: that bakes in the AIPLC rules prompt, workspace tools and a
    session manager, none of which belong in a stateless generation call."""
    async def call(prompt: str) -> str:
        from strands import Agent
        from strands.models import BedrockModel
        model = BedrockModel(model_id=os.environ["ANTHROPIC_MODEL"],
                             max_tokens=8000)
        agent = Agent(model=model, tools=[], callback_handler=None)
        result = await agent.invoke_async(prompt)
        return str(result)
    return call
```

라우터 include (파일 말미, 기존 패턴과 동일):

```python
from pathfinder.routes import surveys  # noqa: E402
app.include_router(surveys.router)
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_routes_surveys.py -v`
Expected: PASS (10 tests)

- [ ] **Step 6: 커밋**

```bash
git add backend/pathfinder/routes/surveys.py backend/pathfinder/app.py backend/tests/test_routes_surveys.py
git commit -m "feat(survey): admin routes (create/get/close/csv) + app wiring

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: 공개 라우트 (토큰 전용)

**Files:**
- Create: `backend/pathfinder/routes/surveys_public.py`
- Modify: `backend/pathfinder/app.py` (router include)
- Test: `backend/tests/test_routes_surveys_public.py`

**Interfaces:**
- Produces:
  - `GET /survey/{token}` → `{title, hypothesis, questions}`. **`project_id`/`slug`/`token`/집계 미포함.** 없는 토큰 404, 마감 410
  - `POST /survey/{token}` body `{answers: {...}}` → 204. 마감 410, 없는 토큰 404, 스키마 밖 키·타입 불일치 400, 크기 초과 413, 응답 1000건 초과 429
- Produces (상수): `MAX_ANSWER_CHARS = 2000`, `MAX_BODY_BYTES = 32 * 1024`, `MAX_RESPONSES = 1000`
- Consumes: Task 3·4 `SurveyStore`(+`resolve_token`), app.py의 `surveys_root_s3_factory`/`survey_store_factory`

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/tests/test_routes_surveys_public.py`:

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && .venv/bin/pytest tests/test_routes_surveys_public.py -v`
Expected: FAIL — 404 (라우트 미등록)

- [ ] **Step 3: 구현**

`backend/pathfinder/routes/surveys_public.py`:

```python
# backend/pathfinder/routes/surveys_public.py — PUBLIC survey routes.
#
# This is the only UNAUTHENTICATED WRITE path in the app, so it is deliberately
# narrow (spec §7):
#   - the token is the only credential; nothing here takes a project id
#   - responses never echo internal identifiers (project_id / slug / token)
#   - only keys defined by the questionnaire are stored (no arbitrary payload)
#   - size and count caps bound S3 growth
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.responses import Response

from pathfinder.survey.models import (SCALE_MAX, SCALE_MIN, Questionnaire,
                                      SurveyResponse)
from pathfinder.survey.store import SurveyStore

_log = logging.getLogger(__name__)

router = APIRouter()

MAX_ANSWER_CHARS = 2000
MAX_BODY_BYTES = 32 * 1024
MAX_RESPONSES = 1000


class AnswersBody(BaseModel):
    answers: dict[str, object]


async def _resolve(token: str) -> tuple[SurveyStore, Questionnaire]:
    import pathfinder.app as app_module
    try:
        pid, slug = await SurveyStore.resolve_token(
            app_module.surveys_root_s3_factory(), token)
    except FileNotFoundError:
        # Do not distinguish "no such token" from "no such survey": that would
        # let a prober learn which tokens exist.
        raise HTTPException(status_code=404, detail="survey not found")
    store = app_module.survey_store_factory(pid, slug)
    try:
        qn = await store.load_questionnaire()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="survey not found")
    if qn.status == "closed":
        raise HTTPException(status_code=410, detail="이 설문은 마감되었습니다.")
    return store, qn


@router.get("/survey/{token}")
async def public_get_survey(token: str):
    _, qn = await _resolve(token)
    # Questions only: no token echo, no project_id/slug, no aggregate.
    return {"title": qn.title, "hypothesis": qn.hypothesis,
            "questions": [q.model_dump() for q in qn.questions]}


def _validate_answers(qn: Questionnaire, answers: dict) -> dict:
    by_id = {q.id: q for q in qn.questions}
    unknown = set(answers) - set(by_id)
    if unknown:
        raise HTTPException(status_code=400,
                            detail=f"unknown question(s): {sorted(unknown)}")
    total = 0
    clean: dict = {}
    for qid, value in answers.items():
        q = by_id[qid]
        if q.type == "scale":
            if isinstance(value, bool) or not isinstance(value, int):
                raise HTTPException(status_code=400,
                                    detail=f"{qid}: scale answer must be an integer")
            if not SCALE_MIN <= value <= SCALE_MAX:
                raise HTTPException(status_code=400,
                                    detail=f"{qid}: scale answer out of range")
            clean[qid] = value
            continue
        if not isinstance(value, str):
            raise HTTPException(status_code=400,
                                detail=f"{qid}: answer must be a string")
        if len(value) > MAX_ANSWER_CHARS:
            raise HTTPException(status_code=413, detail=f"{qid}: answer too long")
        if q.type == "choice" and value not in q.options:
            raise HTTPException(status_code=400,
                                detail=f"{qid}: option not offered")
        total += len(value.encode("utf-8"))
        clean[qid] = value
    if total > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="response too large")
    missing = [q.id for q in qn.questions
               if q.required and q.id not in clean]
    if missing:
        raise HTTPException(status_code=400,
                            detail=f"missing required answer(s): {missing}")
    return clean


@router.post("/survey/{token}", status_code=204)
async def public_submit_survey(token: str, body: AnswersBody):
    store, qn = await _resolve(token)
    if await store.response_count() >= MAX_RESPONSES:
        raise HTTPException(status_code=429,
                            detail="응답 수 상한에 도달했습니다. 설문을 마감해 주세요.")
    clean = _validate_answers(qn, body.answers)

    resp = SurveyResponse(response_id=uuid.uuid4().hex,
                          submitted_at=datetime.now(timezone.utc).isoformat(),
                          answers=clean)
    await store.append_response(resp)   # this PUT is what commits the response
    try:
        await store.refresh_rollup()
    except Exception:
        # The rollup is only a cache: a failure here must not lose a
        # respondent's submission. The next dashboard read rebuilds it.
        _log.exception("rollup refresh failed after response append")
    return Response(status_code=204)
```

app.py 라우터 include 추가:

```python
from pathfinder.routes import surveys_public  # noqa: E402
app.include_router(surveys_public.router)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && .venv/bin/pytest tests/test_routes_surveys_public.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: 전체 스위트 + 성능 회귀 가드 추가**

`backend/tests/test_survey_store_responses.py`에 추가:

```python
async def test_dashboard_read_is_constant_s3_calls():
    """The dashboard must not scale its S3 calls with response count: one
    rollup get + one list, regardless of size (spec §2 measured individual
    parallel gets at 2.61s for 500 responses)."""
    store, project_s3, _ = await _seeded(40)
    await store.refresh_rollup(NOW)

    gets: list[str] = []
    original_get = project_s3.get

    async def counting_get(key):
        gets.append(key)
        return await original_get(key)

    project_s3.get = counting_get
    await store.get_rollup(NOW)
    assert gets == [rollup_key(SLUG)]     # no per-response gets
```

Run: `cd backend && .venv/bin/pytest`
Expected: 전체 green

- [ ] **Step 6: 커밋**

```bash
git add backend/pathfinder/routes/surveys_public.py backend/pathfinder/app.py backend/tests/test_routes_surveys_public.py backend/tests/test_survey_store_responses.py
git commit -m "feat(survey): public token-only routes with schema/size/count caps

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: 프론트 API 클라이언트

**Files:**
- Create: `frontend/lib/api/surveys.ts`
- Test: `frontend/lib/api/surveys.test.ts`

**Interfaces:**
- Produces (타입): `SurveyQuestionType = "scale" | "choice" | "text"`; `SurveyQuestion {id, text, type, options, required}`; `Questionnaire {token, status, title, hypothesis, questions, created_at, closed_at}`; `ScaleStat`/`ChoiceStat`/`TextStat`/`Rollup {count, rebuilt_at, per_question}`; `SurveyView {questionnaire, url, rollup}`; `PublicSurvey {title, hypothesis, questions}`
- Produces (함수): `createSurvey(pid, slug)`, `getSurvey(pid, slug): Promise<SurveyView | null>`(404→null), `closeSurvey(pid, slug)`, `surveyCsvUrl(pid, slug): string`, `getPublicSurvey(token): Promise<PublicSurvey>`(410→`SurveyClosedError`), `submitPublicSurvey(token, answers): Promise<void>`(410→`SurveyClosedError`)
- Consumes: 기존 `lib/api/client.ts`의 `API_BASE_URL`, `ApiError`
- **Task 8 참고**: 이전 슬라이스에서 `request<T>`/`openStream`이 unexported라 `prototypes.ts`가 복제했다(레저 Minor). 이번에는 **`lib/api/http.ts`로 추출**해 `surveys.ts`가 재사용하고, `prototypes.ts`도 그것을 쓰도록 정리한다(3번째 복제 금지).

- [ ] **Step 1: 실패하는 테스트 작성**

`frontend/lib/api/surveys.test.ts`:

```ts
import { describe, it, expect, beforeEach } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "@/test/msw/server";
import { API_BASE_URL } from "./client";
import {
  createSurvey, getSurvey, closeSurvey, surveyCsvUrl,
  getPublicSurvey, submitPublicSurvey, SurveyClosedError,
} from "./surveys";

const PID = "p1";
const SLUG = "demo";

const QUESTIONS = [
  { id: "q1", text: "유용?", type: "scale", options: [], required: true },
  { id: "q2", text: "어느 것?", type: "choice", options: ["A", "B"], required: true },
];

describe("surveys api", () => {
  it("createSurvey returns token and url", async () => {
    server.use(http.post(`${API_BASE_URL}/projects/${PID}/prototypes/${SLUG}/survey`,
      () => HttpResponse.json({ token: "tok", url: "/survey/tok", questions: QUESTIONS },
        { status: 201 })));
    const out = await createSurvey(PID, SLUG);
    expect(out.token).toBe("tok");
    expect(out.url).toBe("/survey/tok");
  });

  it("getSurvey returns null on 404 (no survey yet)", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/${PID}/prototypes/${SLUG}/survey`,
      () => new HttpResponse(null, { status: 404 })));
    expect(await getSurvey(PID, SLUG)).toBeNull();
  });

  it("getSurvey returns questionnaire + rollup", async () => {
    server.use(http.get(`${API_BASE_URL}/projects/${PID}/prototypes/${SLUG}/survey`,
      () => HttpResponse.json({
        questionnaire: { token: "tok", status: "open", title: "t", hypothesis: "h",
                         questions: QUESTIONS, created_at: "x", closed_at: null },
        url: "/survey/tok",
        rollup: { count: 2, rebuilt_at: "x", per_question: {
          q1: { type: "scale", n: 2, mean: 4.5, distribution: { "1": 0, "2": 0, "3": 0, "4": 1, "5": 1 } },
          q2: { type: "choice", n: 2, counts: { A: 2, B: 0 } },
        } },
      })));
    const view = await getSurvey(PID, SLUG);
    expect(view?.rollup.count).toBe(2);
    expect(view?.questionnaire.status).toBe("open");
  });

  it("closeSurvey resolves on 204", async () => {
    server.use(http.post(`${API_BASE_URL}/projects/${PID}/prototypes/${SLUG}/survey/close`,
      () => new HttpResponse(null, { status: 204 })));
    await expect(closeSurvey(PID, SLUG)).resolves.toBeUndefined();
  });

  it("surveyCsvUrl points at the export route", () => {
    expect(surveyCsvUrl(PID, SLUG)).toBe(
      `${API_BASE_URL}/projects/${PID}/prototypes/${SLUG}/survey/responses.csv`);
  });

  it("getPublicSurvey returns questions", async () => {
    server.use(http.get(`${API_BASE_URL}/survey/tok`,
      () => HttpResponse.json({ title: "t", hypothesis: "h", questions: QUESTIONS })));
    const s = await getPublicSurvey("tok");
    expect(s.questions).toHaveLength(2);
  });

  it("getPublicSurvey throws SurveyClosedError on 410", async () => {
    server.use(http.get(`${API_BASE_URL}/survey/tok`,
      () => new HttpResponse(null, { status: 410 })));
    await expect(getPublicSurvey("tok")).rejects.toBeInstanceOf(SurveyClosedError);
  });

  it("submitPublicSurvey resolves on 204 and throws SurveyClosedError on 410", async () => {
    server.use(http.post(`${API_BASE_URL}/survey/tok`,
      () => new HttpResponse(null, { status: 204 })));
    await expect(submitPublicSurvey("tok", { q1: 4 })).resolves.toBeUndefined();

    server.use(http.post(`${API_BASE_URL}/survey/tok`,
      () => new HttpResponse(null, { status: 410 })));
    await expect(submitPublicSurvey("tok", { q1: 4 }))
      .rejects.toBeInstanceOf(SurveyClosedError);
  });
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd frontend && npm test -- --run lib/api/surveys.test.ts`
Expected: FAIL — cannot resolve `./surveys`

- [ ] **Step 3: 공용 fetch 헬퍼 추출 + surveys.ts 구현**

`frontend/lib/api/http.ts` (신규 — `client.ts`의 `request` 로직을 그대로 옮기되 204 허용):

```ts
// Shared fetch wrapper. Extracted so surveys.ts/prototypes.ts don't each carry
// a copy (client.ts's own request() is unexported and assumes a JSON body,
// which 204 responses don't have).
import { API_BASE_URL, ApiError } from "./client";
import { getAuthToken } from "@/lib/auth";

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T | null> {
  const token = getAuthToken();
  const headers: Record<string, string> = {
    ...(init?.body ? { "Content-Type": "application/json" } : {}),
    ...(token ? { "X-Project-Token": token } : {}),
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  const res = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* non-JSON error body — keep statusText */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return null;
  return (await res.json()) as T;
}
```

`frontend/lib/api/surveys.ts`:

```ts
// Validation-survey API: admin calls (behind the app's auth) and the two
// public token-only calls used by /survey/[token].
import { ApiError } from "./client";
import { API_BASE_URL } from "./client";
import { apiFetch } from "./http";

export type SurveyQuestionType = "scale" | "choice" | "text";

export interface SurveyQuestion {
  id: string;
  text: string;
  type: SurveyQuestionType;
  options: string[];
  required: boolean;
}

export interface Questionnaire {
  token: string;
  status: "open" | "closed";
  title: string;
  hypothesis: string;
  questions: SurveyQuestion[];
  created_at: string;
  closed_at: string | null;
}

export interface ScaleStat {
  type: "scale";
  n: number;
  mean: number;
  distribution: Record<string, number>;
}
export interface ChoiceStat { type: "choice"; n: number; counts: Record<string, number> }
export interface TextStat { type: "text"; n: number; samples: string[] }
export type Stat = ScaleStat | ChoiceStat | TextStat;

export interface Rollup {
  count: number;
  rebuilt_at: string;
  per_question: Record<string, Stat>;
}

export interface SurveyView {
  questionnaire: Questionnaire;
  url: string;
  rollup: Rollup;
}

export interface PublicSurvey {
  title: string;
  hypothesis: string;
  questions: SurveyQuestion[];
}

export type AnswerValue = string | number;

/** A closed survey is a normal end state, not a failure — the public form
 *  shows a "마감되었습니다" screen rather than an error. */
export class SurveyClosedError extends Error {
  constructor() {
    super("survey closed");
    this.name = "SurveyClosedError";
  }
}

function base(pid: string, slug: string): string {
  return `/projects/${encodeURIComponent(pid)}/prototypes/${encodeURIComponent(slug)}/survey`;
}

export async function createSurvey(pid: string, slug: string): Promise<{
  token: string; url: string; questions: SurveyQuestion[];
}> {
  return (await apiFetch(base(pid, slug), { method: "POST" }))!;
}

export async function getSurvey(pid: string, slug: string): Promise<SurveyView | null> {
  try {
    return (await apiFetch<SurveyView>(base(pid, slug)))!;
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

export async function closeSurvey(pid: string, slug: string): Promise<void> {
  await apiFetch(`${base(pid, slug)}/close`, { method: "POST" });
}

export function surveyCsvUrl(pid: string, slug: string): string {
  return `${API_BASE_URL}${base(pid, slug)}/responses.csv`;
}

export async function getPublicSurvey(token: string): Promise<PublicSurvey> {
  try {
    return (await apiFetch<PublicSurvey>(`/survey/${encodeURIComponent(token)}`))!;
  } catch (err) {
    if (err instanceof ApiError && err.status === 410) throw new SurveyClosedError();
    throw err;
  }
}

export async function submitPublicSurvey(
  token: string, answers: Record<string, AnswerValue>,
): Promise<void> {
  try {
    await apiFetch(`/survey/${encodeURIComponent(token)}`, {
      method: "POST",
      body: JSON.stringify({ answers }),
    });
  } catch (err) {
    if (err instanceof ApiError && err.status === 410) throw new SurveyClosedError();
    throw err;
  }
}
```

- [ ] **Step 4: 테스트 통과 + 기존 스위트 회귀 확인**

Run: `cd frontend && npm test -- --run && npx tsc --noEmit`
Expected: 신규 8 PASS, 기존 전부 green, tsc clean

- [ ] **Step 5: 커밋**

```bash
git add frontend/lib/api/http.ts frontend/lib/api/surveys.ts frontend/lib/api/surveys.test.ts
git commit -m "feat(frontend): survey API client + shared apiFetch helper

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: SurveyPanel + 대시보드 (프로토타입 탭)

**Files:**
- Create: `frontend/components/prototypes/SurveyDashboard.tsx`
- Create: `frontend/components/prototypes/SurveyPanel.tsx`
- Modify: `frontend/app/projects/[projectId]/prototypes/page.tsx` (패널 마운트)
- Test: `frontend/components/prototypes/SurveyDashboard.test.tsx`, `frontend/components/prototypes/SurveyPanel.test.tsx`

**Interfaces:**
- Produces: `SurveyDashboard({ questions, rollup }: { questions: SurveyQuestion[]; rollup: Rollup })`; `SurveyPanel({ projectId, slug }: { projectId: string; slug: string })`
- Consumes: Task 8의 `getSurvey`/`createSurvey`/`closeSurvey`/`surveyCsvUrl`, 기존 `useAsync`(lib/useAsync.ts), 카드 스타일 idiom(`components/canvas/ArtifactCard.tsx`)
- 상태: 설문 없음 → "질문 생성" 버튼 / 열림 → 링크 복사 + 응답 수 + 대시보드 + 마감 / 마감 → 대시보드 + CSV + "새 설문 생성"
- 대시보드는 **수동 새로고침** (SSE 없음, 스펙 §9) — "새로고침" 버튼 제공

- [ ] **Step 1: 실패하는 테스트 작성**

`frontend/components/prototypes/SurveyDashboard.test.tsx`:

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SurveyDashboard } from "./SurveyDashboard";
import type { SurveyQuestion, Rollup } from "@/lib/api/surveys";

const QUESTIONS: SurveyQuestion[] = [
  { id: "q1", text: "유용했나요?", type: "scale", options: [], required: true },
  { id: "q2", text: "어느 기능?", type: "choice", options: ["요약", "검색"], required: true },
  { id: "q3", text: "개선점", type: "text", options: [], required: false },
];

const ROLLUP: Rollup = {
  count: 3, rebuilt_at: "2026-07-25T00:00:00Z",
  per_question: {
    q1: { type: "scale", n: 3, mean: 4.33,
          distribution: { "1": 0, "2": 0, "3": 1, "4": 0, "5": 2 } },
    q2: { type: "choice", n: 3, counts: { 요약: 2, 검색: 1 } },
    q3: { type: "text", n: 1, samples: ["속도가 느립니다"] },
  },
};

describe("SurveyDashboard", () => {
  it("shows the response count", () => {
    render(<SurveyDashboard questions={QUESTIONS} rollup={ROLLUP} />);
    expect(screen.getByText(/3/)).toBeInTheDocument();
  });

  it("renders scale mean and each question text", () => {
    render(<SurveyDashboard questions={QUESTIONS} rollup={ROLLUP} />);
    expect(screen.getByText("유용했나요?")).toBeInTheDocument();
    expect(screen.getByText(/4\.33/)).toBeInTheDocument();
  });

  it("renders choice counts including an option with zero picks", () => {
    const rollup: Rollup = {
      ...ROLLUP,
      per_question: { ...ROLLUP.per_question,
        q2: { type: "choice", n: 2, counts: { 요약: 2, 검색: 0 } } },
    };
    render(<SurveyDashboard questions={QUESTIONS} rollup={rollup} />);
    expect(screen.getByText("검색")).toBeInTheDocument();
  });

  it("renders text samples", () => {
    render(<SurveyDashboard questions={QUESTIONS} rollup={ROLLUP} />);
    expect(screen.getByText("속도가 느립니다")).toBeInTheDocument();
  });

  it("shows an empty state when there are no responses", () => {
    const empty: Rollup = { count: 0, rebuilt_at: "x", per_question: {
      q1: { type: "scale", n: 0, mean: 0, distribution: { "1": 0, "2": 0, "3": 0, "4": 0, "5": 0 } },
      q2: { type: "choice", n: 0, counts: { 요약: 0, 검색: 0 } },
      q3: { type: "text", n: 0, samples: [] },
    } };
    render(<SurveyDashboard questions={QUESTIONS} rollup={empty} />);
    expect(screen.getByText(/아직 응답이 없습니다/)).toBeInTheDocument();
  });
});
```

`frontend/components/prototypes/SurveyPanel.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SurveyPanel } from "./SurveyPanel";
import * as api from "@/lib/api/surveys";

const PID = "p1";
const SLUG = "demo";
const QUESTIONS: api.SurveyQuestion[] = [
  { id: "q1", text: "유용?", type: "scale", options: [], required: true },
];
const OPEN_VIEW: api.SurveyView = {
  questionnaire: { token: "tok", status: "open", title: "검증 설문",
                   hypothesis: "가설", questions: QUESTIONS,
                   created_at: "x", closed_at: null },
  url: "/survey/tok",
  rollup: { count: 0, rebuilt_at: "x", per_question: {
    q1: { type: "scale", n: 0, mean: 0,
          distribution: { "1": 0, "2": 0, "3": 0, "4": 0, "5": 0 } } } },
};

beforeEach(() => vi.restoreAllMocks());

describe("SurveyPanel", () => {
  it("offers generation when no survey exists", async () => {
    vi.spyOn(api, "getSurvey").mockResolvedValue(null);
    render(<SurveyPanel projectId={PID} slug={SLUG} />);
    expect(await screen.findByRole("button", { name: /질문 생성/ })).toBeInTheDocument();
  });

  it("creates a survey and reloads the view", async () => {
    const getSurvey = vi.spyOn(api, "getSurvey")
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce(OPEN_VIEW);
    const createSurvey = vi.spyOn(api, "createSurvey")
      .mockResolvedValue({ token: "tok", url: "/survey/tok", questions: QUESTIONS });

    render(<SurveyPanel projectId={PID} slug={SLUG} />);
    await userEvent.click(await screen.findByRole("button", { name: /질문 생성/ }));

    await waitFor(() => expect(createSurvey).toHaveBeenCalledWith(PID, SLUG));
    expect(getSurvey).toHaveBeenCalledTimes(2);
  });

  it("shows the public link for an open survey", async () => {
    vi.spyOn(api, "getSurvey").mockResolvedValue(OPEN_VIEW);
    render(<SurveyPanel projectId={PID} slug={SLUG} />);
    expect(await screen.findByText(/\/survey\/tok/)).toBeInTheDocument();
  });

  it("closes the survey", async () => {
    vi.spyOn(api, "getSurvey").mockResolvedValue(OPEN_VIEW);
    const closeSurvey = vi.spyOn(api, "closeSurvey").mockResolvedValue();
    render(<SurveyPanel projectId={PID} slug={SLUG} />);
    await userEvent.click(await screen.findByRole("button", { name: /설문 마감/ }));
    await waitFor(() => expect(closeSurvey).toHaveBeenCalledWith(PID, SLUG));
  });

  it("offers CSV export and regeneration once closed", async () => {
    vi.spyOn(api, "getSurvey").mockResolvedValue({
      ...OPEN_VIEW,
      questionnaire: { ...OPEN_VIEW.questionnaire, status: "closed",
                       closed_at: "2026-07-26T00:00:00Z" },
    });
    render(<SurveyPanel projectId={PID} slug={SLUG} />);
    expect(await screen.findByRole("link", { name: /CSV/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /새 설문 생성/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /설문 마감/ })).not.toBeInTheDocument();
  });

  it("surfaces a generation failure without wedging the panel", async () => {
    vi.spyOn(api, "getSurvey").mockResolvedValue(null);
    vi.spyOn(api, "createSurvey").mockRejectedValue(new Error("boom"));
    render(<SurveyPanel projectId={PID} slug={SLUG} />);
    await userEvent.click(await screen.findByRole("button", { name: /질문 생성/ }));
    expect(await screen.findByText(/실패/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /질문 생성/ })).toBeEnabled();
  });
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd frontend && npm test -- --run components/prototypes/Survey`
Expected: FAIL — cannot resolve `./SurveyDashboard` / `./SurveyPanel`

- [ ] **Step 3: SurveyDashboard 구현**

`frontend/components/prototypes/SurveyDashboard.tsx`:

```tsx
"use client";
import type { Rollup, SurveyQuestion } from "@/lib/api/surveys";

function ScaleBar({ label, n, max }: { label: string; n: number; max: number }) {
  const pct = max > 0 ? Math.round((n / max) * 100) : 0;
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-4 text-slate-400">{label}</span>
      <div className="flex-1 h-2 rounded-full bg-slate-100 overflow-hidden">
        <div className="h-full bg-violet-500" style={{ width: `${pct}%` }} />
      </div>
      <span className="w-8 text-right text-slate-500">{n}</span>
    </div>
  );
}

export function SurveyDashboard({ questions, rollup }: {
  questions: SurveyQuestion[];
  rollup: Rollup;
}) {
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-500">
        응답 <span className="font-bold text-slate-800">{rollup.count}</span>건
      </p>
      {rollup.count === 0 && (
        <p className="text-sm text-slate-400">
          아직 응답이 없습니다. 링크를 공유해 응답을 받아보세요.
        </p>
      )}
      {questions.map((q) => {
        const stat = rollup.per_question[q.id];
        if (!stat) return null;
        return (
          <div key={q.id} className="rounded-xl border border-slate-200 p-4">
            <p className="text-sm font-medium text-slate-700 mb-3">{q.text}</p>
            {stat.type === "scale" && (
              <div className="space-y-1.5">
                <p className="text-xs text-slate-500 mb-2">
                  평균 <span className="font-bold text-violet-600">{stat.mean}</span>
                  {" "}/ 5 · 응답 {stat.n}건
                </p>
                {["5", "4", "3", "2", "1"].map((k) => (
                  <ScaleBar key={k} label={k} n={stat.distribution[k] ?? 0}
                            max={Math.max(...Object.values(stat.distribution), 1)} />
                ))}
              </div>
            )}
            {stat.type === "choice" && (
              <ul className="space-y-1.5">
                {Object.entries(stat.counts).map(([opt, n]) => (
                  <li key={opt} className="flex items-center gap-2 text-xs">
                    <span className="flex-1 text-slate-600">{opt}</span>
                    <span className="text-slate-500">{n}건</span>
                  </li>
                ))}
              </ul>
            )}
            {stat.type === "text" && (
              <div>
                <p className="text-xs text-slate-500 mb-2">자유 응답 {stat.n}건</p>
                <ul className="space-y-2">
                  {stat.samples.map((s, i) => (
                    <li key={i} className="rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600">
                      {s}
                    </li>
                  ))}
                </ul>
                {stat.n > stat.samples.length && (
                  <p className="text-xs text-slate-400 mt-2">
                    전체 응답은 CSV로 내보내 확인하세요.
                  </p>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 4: SurveyPanel 구현**

`frontend/components/prototypes/SurveyPanel.tsx`:

```tsx
"use client";
import { useCallback, useEffect, useState } from "react";
import {
  closeSurvey, createSurvey, getSurvey, surveyCsvUrl,
  type SurveyView,
} from "@/lib/api/surveys";

export function SurveyPanel({ projectId, slug }: { projectId: string; slug: string }) {
  const [view, setView] = useState<SurveyView | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setView(await getSurvey(projectId, slug));
    } catch {
      setError("설문 정보를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [projectId, slug]);

  useEffect(() => { void reload(); }, [reload]);

  async function handleCreate() {
    setBusy(true);
    setError(null);
    try {
      await createSurvey(projectId, slug);
      await reload();
    } catch {
      setError("질문 생성에 실패했습니다. 다시 시도해 주세요.");
    } finally {
      setBusy(false);
    }
  }

  async function handleClose() {
    setBusy(true);
    try {
      await closeSurvey(projectId, slug);
      await reload();
    } catch {
      setError("설문 마감에 실패했습니다.");
    } finally {
      setBusy(false);
    }
  }

  const qn = view?.questionnaire;
  const publicUrl = view ? `${window.location.origin}${view.url}` : "";

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-bold text-slate-400 uppercase tracking-wide">
          검증 설문
        </h2>
        {view && (
          <button type="button" onClick={() => void reload()} disabled={busy}
                  className="text-xs text-slate-500 hover:text-slate-700">
            새로고침
          </button>
        )}
      </div>

      {error && <p className="text-sm text-rose-600">{error}</p>}
      {loading && !view && <p className="text-sm text-slate-400">불러오는 중…</p>}

      {!loading && !view && (
        <div className="rounded-xl border border-slate-200 p-4">
          <p className="text-sm text-slate-600 mb-3">
            프로토타입 명세의 검증 가설에서 설문 문항을 생성합니다.
          </p>
          <button type="button" onClick={() => void handleCreate()} disabled={busy}
                  className="px-3.5 py-2 rounded-lg bg-violet-600 text-white text-sm font-medium disabled:opacity-50">
            {busy ? "생성 중…" : "질문 생성"}
          </button>
        </div>
      )}

      {qn && (
        <>
          <div className="rounded-xl border border-slate-200 p-4 space-y-3">
            <p className="text-sm font-medium text-slate-700">{qn.title}</p>
            {qn.status === "open" ? (
              <>
                <p className="text-xs text-slate-500 break-all">{view!.url}</p>
                <div className="flex flex-wrap gap-2">
                  <button type="button"
                          onClick={() => {
                            void navigator.clipboard?.writeText(publicUrl);
                            setCopied(true);
                          }}
                          className="px-3 py-1.5 rounded-lg border border-slate-200 text-xs hover:bg-slate-50">
                    {copied ? "복사됨" : "링크 복사"}
                  </button>
                  <button type="button" onClick={() => void handleClose()} disabled={busy}
                          className="px-3 py-1.5 rounded-lg border border-slate-200 text-xs hover:bg-slate-50 disabled:opacity-50">
                    설문 마감
                  </button>
                </div>
              </>
            ) : (
              <div className="flex flex-wrap gap-2 items-center">
                <span className="px-2 py-0.5 rounded-full bg-slate-100 text-slate-600 text-xs">
                  마감됨
                </span>
                <a href={surveyCsvUrl(projectId, slug)}
                   className="px-3 py-1.5 rounded-lg border border-slate-200 text-xs hover:bg-slate-50">
                  CSV 내보내기
                </a>
                <button type="button" onClick={() => void handleCreate()} disabled={busy}
                        className="px-3 py-1.5 rounded-lg border border-slate-200 text-xs hover:bg-slate-50 disabled:opacity-50">
                  새 설문 생성
                </button>
              </div>
            )}
          </div>
          <SurveyDashboard questions={qn.questions} rollup={view!.rollup} />
        </>
      )}
    </section>
  );
}
```

`SurveyPanel.tsx` 상단 import 블록(정적 import — Next 클라이언트 컴포넌트에서
`require`는 번들러 경고·SSR 불일치를 유발한다):

```tsx
import { SurveyDashboard } from "./SurveyDashboard";
```

- [ ] **Step 5: 프로토타입 페이지에 마운트**

`frontend/app/projects/[projectId]/prototypes/page.tsx` — 카드 그리드 아래, `BuildPanel` 렌더 조건 밖에 추가. 기존 파일의 카드 목록을 렌더하는 `</div>` 뒤에 삽입하고, 열린 프로토타입(`openSlug`)이 있을 때만 노출한다:

```tsx
{openSlug && <SurveyPanel projectId={projectId} slug={openSlug} />}
```

상단에 `import { SurveyPanel } from "@/components/prototypes/SurveyPanel";` 추가.

- [ ] **Step 6: 테스트 통과 확인**

Run: `cd frontend && npm test -- --run && npx tsc --noEmit && npm run build`
Expected: 전부 PASS / clean / build 성공

- [ ] **Step 7: 커밋**

```bash
git add frontend/components/prototypes/SurveyPanel.tsx frontend/components/prototypes/SurveyDashboard.tsx frontend/components/prototypes/SurveyPanel.test.tsx frontend/components/prototypes/SurveyDashboard.test.tsx "frontend/app/projects/[projectId]/prototypes/page.tsx"
git commit -m "feat(frontend): survey panel with link/close/CSV + aggregate dashboard

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: 공개 응답 폼

**Files:**
- Create: `frontend/components/survey/SurveyForm.tsx`
- Create: `frontend/app/survey/[token]/page.tsx`
- Test: `frontend/components/survey/SurveyForm.test.tsx`, `frontend/app/survey/[token]/page.test.tsx`

**Interfaces:**
- Produces: `SurveyForm({ questions, onSubmit, submitting }: { questions: SurveyQuestion[]; onSubmit: (answers: Record<string, AnswerValue>) => void; submitting: boolean })`; 페이지는 `use(params)`로 token 추출(기존 `workspace/page.tsx` 패턴)
- Consumes: Task 8의 `getPublicSurvey`/`submitPublicSurvey`/`SurveyClosedError`
- 페이지는 **Pathfinder 헤더·인증 없이 독립 렌더** (공개 응답자용)
- 제출 성공 → 완료 화면 고정(중복 제출 억제, 스펙 §9)

- [ ] **Step 1: 실패하는 테스트 작성**

`frontend/components/survey/SurveyForm.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SurveyForm } from "./SurveyForm";
import type { SurveyQuestion } from "@/lib/api/surveys";

const QUESTIONS: SurveyQuestion[] = [
  { id: "q1", text: "유용했나요?", type: "scale", options: [], required: true },
  { id: "q2", text: "어느 기능?", type: "choice", options: ["요약", "검색"], required: true },
  { id: "q3", text: "개선점", type: "text", options: [], required: false },
];

describe("SurveyForm", () => {
  it("renders all three question types", () => {
    render(<SurveyForm questions={QUESTIONS} onSubmit={vi.fn()} submitting={false} />);
    expect(screen.getByText("유용했나요?")).toBeInTheDocument();
    expect(screen.getByLabelText("요약")).toBeInTheDocument();
    expect(screen.getByText("개선점")).toBeInTheDocument();
  });

  it("blocks submit until required answers are given", async () => {
    const onSubmit = vi.fn();
    render(<SurveyForm questions={QUESTIONS} onSubmit={onSubmit} submitting={false} />);
    await userEvent.click(screen.getByRole("button", { name: /제출/ }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByText(/필수/)).toBeInTheDocument();
  });

  it("submits scale as a number and choice as its label", async () => {
    const onSubmit = vi.fn();
    render(<SurveyForm questions={QUESTIONS} onSubmit={onSubmit} submitting={false} />);
    await userEvent.click(screen.getByRole("radio", { name: "4" }));
    await userEvent.click(screen.getByLabelText("요약"));
    await userEvent.type(screen.getByLabelText("개선점"), "속도 개선");
    await userEvent.click(screen.getByRole("button", { name: /제출/ }));
    expect(onSubmit).toHaveBeenCalledWith({ q1: 4, q2: "요약", q3: "속도 개선" });
  });

  it("omits an untouched optional text answer", async () => {
    const onSubmit = vi.fn();
    render(<SurveyForm questions={QUESTIONS} onSubmit={onSubmit} submitting={false} />);
    await userEvent.click(screen.getByRole("radio", { name: "5" }));
    await userEvent.click(screen.getByLabelText("검색"));
    await userEvent.click(screen.getByRole("button", { name: /제출/ }));
    expect(onSubmit).toHaveBeenCalledWith({ q1: 5, q2: "검색" });
  });

  it("disables the submit button while submitting", () => {
    render(<SurveyForm questions={QUESTIONS} onSubmit={vi.fn()} submitting />);
    expect(screen.getByRole("button", { name: /제출/ })).toBeDisabled();
  });
});
```

`frontend/app/survey/[token]/page.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SurveyPage from "./page";
import * as api from "@/lib/api/surveys";

const QUESTIONS: api.SurveyQuestion[] = [
  { id: "q1", text: "유용했나요?", type: "scale", options: [], required: true },
];

beforeEach(() => vi.restoreAllMocks());

function renderPage() {
  return render(<SurveyPage params={Promise.resolve({ token: "tok" })} />);
}

describe("public survey page", () => {
  it("renders the questionnaire", async () => {
    vi.spyOn(api, "getPublicSurvey").mockResolvedValue({
      title: "검증 설문", hypothesis: "가설", questions: QUESTIONS });
    renderPage();
    expect(await screen.findByText("검증 설문")).toBeInTheDocument();
  });

  it("shows a closed notice for a closed survey", async () => {
    vi.spyOn(api, "getPublicSurvey").mockRejectedValue(new api.SurveyClosedError());
    renderPage();
    expect(await screen.findByText(/마감/)).toBeInTheDocument();
  });

  it("shows a not-found notice for an unknown token", async () => {
    vi.spyOn(api, "getPublicSurvey").mockRejectedValue(new Error("nope"));
    renderPage();
    expect(await screen.findByText(/찾을 수 없습니다|오류/)).toBeInTheDocument();
  });

  it("shows a thank-you screen after submitting and hides the form", async () => {
    vi.spyOn(api, "getPublicSurvey").mockResolvedValue({
      title: "검증 설문", hypothesis: "가설", questions: QUESTIONS });
    const submit = vi.spyOn(api, "submitPublicSurvey").mockResolvedValue();
    renderPage();
    await userEvent.click(await screen.findByRole("radio", { name: "4" }));
    await userEvent.click(screen.getByRole("button", { name: /제출/ }));
    expect(await screen.findByText(/감사/)).toBeInTheDocument();
    expect(submit).toHaveBeenCalledWith("tok", { q1: 4 });
    // Form is gone: re-submitting the same response is not offered.
    expect(screen.queryByRole("button", { name: /제출/ })).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd frontend && npm test -- --run components/survey app/survey`
Expected: FAIL — cannot resolve `./SurveyForm` / `./page`

- [ ] **Step 3: SurveyForm 구현**

`frontend/components/survey/SurveyForm.tsx`:

```tsx
"use client";
import { useState } from "react";
import type { AnswerValue, SurveyQuestion } from "@/lib/api/surveys";

const SCALE_VALUES = [1, 2, 3, 4, 5];

export function SurveyForm({ questions, onSubmit, submitting }: {
  questions: SurveyQuestion[];
  onSubmit: (answers: Record<string, AnswerValue>) => void;
  submitting: boolean;
}) {
  const [answers, setAnswers] = useState<Record<string, AnswerValue>>({});
  const [showError, setShowError] = useState(false);

  function set(id: string, value: AnswerValue) {
    setAnswers((prev) => ({ ...prev, [id]: value }));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const filled: Record<string, AnswerValue> = {};
    for (const [k, v] of Object.entries(answers)) {
      // Drop untouched optional text so the backend doesn't store empty strings.
      if (typeof v === "string" && v.trim() === "") continue;
      filled[k] = v;
    }
    const missing = questions.filter((q) => q.required && filled[q.id] === undefined);
    if (missing.length > 0) {
      setShowError(true);
      return;
    }
    setShowError(false);
    onSubmit(filled);
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {questions.map((q, idx) => (
        <fieldset key={q.id} className="rounded-xl border border-slate-200 p-4">
          <legend className="px-1 text-sm font-medium text-slate-700">
            {idx + 1}. {q.text}
            {q.required && <span className="text-rose-500 ml-1">*</span>}
          </legend>

          {q.type === "scale" && (
            <div className="flex gap-4 mt-3" role="radiogroup" aria-label={q.text}>
              {SCALE_VALUES.map((v) => (
                <label key={v} className="flex flex-col items-center gap-1 text-xs text-slate-500">
                  <input type="radio" name={q.id} value={v} aria-label={String(v)}
                         checked={answers[q.id] === v}
                         onChange={() => set(q.id, v)} />
                  {v}
                </label>
              ))}
            </div>
          )}

          {q.type === "choice" && (
            <div className="space-y-2 mt-3">
              {q.options.map((opt) => (
                <label key={opt} className="flex items-center gap-2 text-sm text-slate-600">
                  <input type="radio" name={q.id} value={opt} aria-label={opt}
                         checked={answers[q.id] === opt}
                         onChange={() => set(q.id, opt)} />
                  {opt}
                </label>
              ))}
            </div>
          )}

          {q.type === "text" && (
            <textarea id={q.id} aria-label={q.text} rows={3} maxLength={2000}
                      value={String(answers[q.id] ?? "")}
                      onChange={(e) => set(q.id, e.target.value)}
                      className="mt-3 w-full rounded-lg border border-slate-200 p-2 text-sm" />
          )}
        </fieldset>
      ))}

      {showError && (
        <p className="text-sm text-rose-600">필수 문항에 모두 답해 주세요.</p>
      )}

      <button type="submit" disabled={submitting}
              className="px-4 py-2 rounded-lg bg-violet-600 text-white text-sm font-medium disabled:opacity-50">
        {submitting ? "제출 중…" : "제출"}
      </button>
    </form>
  );
}
```

- [ ] **Step 4: 공개 페이지 구현**

`frontend/app/survey/[token]/page.tsx`:

```tsx
"use client";
import { use, useEffect, useState } from "react";
import { SurveyForm } from "@/components/survey/SurveyForm";
import {
  getPublicSurvey, submitPublicSurvey, SurveyClosedError,
  type AnswerValue, type PublicSurvey,
} from "@/lib/api/surveys";

type State =
  | { kind: "loading" }
  | { kind: "ready"; survey: PublicSurvey }
  | { kind: "closed" }
  | { kind: "error" }
  | { kind: "done" };

// Standalone page: no AppHeader, no auth — respondents reach it by token link
// only, and must never see project internals.
export default function SurveyPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const [state, setState] = useState<State>({ kind: "loading" });
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let alive = true;
    getPublicSurvey(token)
      .then((survey) => { if (alive) setState({ kind: "ready", survey }); })
      .catch((err) => {
        if (!alive) return;
        setState({ kind: err instanceof SurveyClosedError ? "closed" : "error" });
      });
    return () => { alive = false; };
  }, [token]);

  async function handleSubmit(answers: Record<string, AnswerValue>) {
    setSubmitting(true);
    try {
      await submitPublicSurvey(token, answers);
      setState({ kind: "done" });
    } catch (err) {
      setState({ kind: err instanceof SurveyClosedError ? "closed" : "error" });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="max-w-2xl mx-auto px-6 py-10">
      {state.kind === "loading" && <p className="text-sm text-slate-400">불러오는 중…</p>}

      {state.kind === "ready" && (
        <>
          <h1 className="text-xl font-bold text-slate-800 mb-2">{state.survey.title}</h1>
          <p className="text-sm text-slate-500 mb-8">
            프로토타입을 사용해 본 경험을 알려주세요. 응답은 익명으로 수집됩니다.
          </p>
          <SurveyForm questions={state.survey.questions}
                      onSubmit={(a) => void handleSubmit(a)}
                      submitting={submitting} />
        </>
      )}

      {state.kind === "done" && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-6">
          <p className="font-medium text-emerald-800">응답해 주셔서 감사합니다.</p>
          <p className="text-sm text-emerald-700 mt-1">제출이 완료되었습니다.</p>
        </div>
      )}

      {state.kind === "closed" && (
        <div className="rounded-xl border border-slate-200 p-6">
          <p className="font-medium text-slate-700">이 설문은 마감되었습니다.</p>
        </div>
      )}

      {state.kind === "error" && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-6">
          <p className="font-medium text-rose-800">설문을 찾을 수 없습니다.</p>
          <p className="text-sm text-rose-700 mt-1">링크를 다시 확인해 주세요.</p>
        </div>
      )}
    </main>
  );
}
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd frontend && npm test -- --run && npx tsc --noEmit && npm run build`
Expected: 전부 PASS / clean / build 성공(`/survey/[token]` 라우트 출력 확인)

- [ ] **Step 6: 커밋**

```bash
git add frontend/components/survey/ "frontend/app/survey/[token]/"
git commit -m "feat(frontend): public token-only survey form with closed/thank-you states

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: 문서 + e2e 체크리스트

**Files:**
- Modify: `docs/superpowers/checklists/2026-07-24-prototype-generation-e2e.md` (설문 절 추가)
- Modify: `README.md` (검증 설문 단락)

**Interfaces:**
- Consumes: Task 1–10의 라우트·UI

- [ ] **Step 1: e2e 체크리스트에 설문 절 추가**

`docs/superpowers/checklists/2026-07-24-prototype-generation-e2e.md` 말미에 추가:

```markdown
## 검증 설문 (2026-07-25 추가)

- [ ] 프로토타입 탭에서 "질문 생성" → 201, 6~10문항 생성 확인
- [ ] `aiplc-docs/discovery/prototypes/{slug}/validation-questionnaire.md` 가
      문서 패널에서 열리는지 확인
- [ ] 공개 링크를 **로그아웃 상태(다른 브라우저/시크릿 창)** 로 열어 문항이 보이는지 확인
- [ ] 공개 응답 본문에 project_id·slug가 없는지 DevTools Network에서 확인
- [ ] 3종 문항(scale/choice/text) 응답 제출 → 완료 화면, 재제출 폼 미노출
- [ ] 대시보드 새로고침 → 응답 수·평균·선택 분포·자유응답 샘플 반영
- [ ] 여러 건 제출 후 S3에 `responses/{uuid}.json` 개수 일치 확인
- [ ] `rollup.json` 을 S3에서 수동 삭제 후 대시보드 새로고침 → 수치 정상 재구축
- [ ] 필수 문항 미응답 제출 → 400, 없는 선택지 전송 → 400 (DevTools로 직접 POST)
- [ ] "설문 마감" → 공개 링크 재방문 시 410 "마감되었습니다" 화면
- [ ] CSV 내보내기 → 문항 헤더·한글 정상, Step 6 종합에 붙여넣기 가능
- [ ] 마감 후 "새 설문 생성" → 이전 응답이 `archive/{closed_at}/` 로 이동하고
      새 설문 집계에 섞이지 않는지 확인
```

- [ ] **Step 2: README에 단락 추가**

`README.md`의 프로토타입 탭 소개 단락 뒤에 추가:

```markdown
프로토타입을 사용자에게 검증할 때는 같은 탭에서 **검증 설문**을 만들 수 있다.
프로토타입 명세의 검증 가설·기능 목록에서 문항을 생성하고(`validation-questionnaire.md`),
인증이 필요 없는 토큰 링크(`/survey/{token}`)를 공유해 익명 응답을 받고, 집계를
대시보드로 확인한 뒤 CSV로 내보내 Discovery의 검증 종합 단계에 넣는다. 응답은 S3에
저장되며(응답 1건 = 객체 1개), 대시보드는 `rollup.json` 캐시를 읽는다.
```

- [ ] **Step 3: 전체 스위트 최종 확인**

Run: `cd backend && .venv/bin/pytest && cd ../frontend && npm test -- --run && npx tsc --noEmit`
Expected: 전부 green

- [ ] **Step 4: 커밋**

```bash
git add docs/superpowers/checklists/2026-07-24-prototype-generation-e2e.md README.md
git commit -m "docs: validation survey e2e checklist + README section

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-Review 결과

**스펙 커버리지**: §1 결정사항(S3·에이전트 1턴·토큰+수동마감·rollup) → T3–T7; §2 성능
근거 → T4 병렬 get + T7 Step 5 호출 카운트 가드; §3 데이터 모델·archive 격리 → T1·T3·T4;
§4 컴포넌트 → T1–T10 전부; §5 라우트 6개 → T6(4개)·T7(2개); §6 흐름 → T6·T7·T9·T10;
§7 에러 표 11행 → T6·T7 테스트로 각각 커버(404/409/410/413/429/400/502 + rollup 실패
무해화); §8 테스트 전략 → 각 태스크 TDD + T7의 성능 회귀 가드; §9 제외 항목 준수
(룰 미수정, 문항 편집 UI 없음, SSE 없음, 캡차·IP 리밋 없음).

**모호성 해소 기록**: (1) 문항 생성에 `StrandsDriver`를 재사용하지 않는다 — 룰
시스템프롬프트·워크스페이스 도구·세션 매니저가 통째로 딸려오므로 stateless 생성에
부적합. 일회성 Strands `Agent`를 쓴다. (2) PROTOTYPE md **헤딩 파싱을 하지 않는다** —
실제 `PROTOTYPE-*.md` 헤딩(`## Use Case Overview`/`### Success Criteria`)은 검증 룰
템플릿과 다르므로, md 전문을 모델에 넘겨 가설·기능을 뽑게 한다. (3) 토큰 생성은
store가 아니라 라우트가 한다(테스트 결정성). (4) 공개 응답의 필수 문항 검증은
서버에서도 한다 — 클라이언트 검증만으로는 직접 POST를 막지 못한다.

**타입 일관성**: `Question`/`Questionnaire`/`SurveyResponse`/`Rollup`이 T1 정의 →
T2 집계 → T3·T4 저장 → T6·T7 라우트 → T8 TS 미러까지 필드명 일치(snake_case 유지:
`per_question`·`created_at`·`closed_at`). `build_rollup(questions, responses, now)`
시그니처가 T2 생산 ↔ T4 소비 일치. `SurveyStore` 메서드명이 T3(save/load/close/
resolve_token) ↔ T4(append_response/response_count/load_responses/refresh_rollup/
get_rollup/archive_current/responses_csv) ↔ T6·T7 호출부 일치. TS `SurveyClosedError`가
T8 생산 ↔ T10 소비 일치.

**초안에서 고친 것**: T9의 대시보드 렌더가 한때 `require()` 기반
`SurveyDashboardSlot`이었다 — 구현자가 그대로 옮겨 적을 함정이라 정적 import로
교체했다(Next 클라이언트 컴포넌트에서 `require`는 번들러 경고·SSR 불일치를 유발).
