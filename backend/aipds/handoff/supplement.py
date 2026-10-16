# backend/aipds/handoff/supplement.py — 인계 시점에 PM에게 받는 보완 답.
#
# **왜 있는가.** 출발점에 따라 어떤 주제의 재료가 없다(handoff/readiness). Path B와 EP1은
# Envision을 거치지 않아 문제의 근거·가정·하지 않을 것이 비고, 그것은 PM만 답할 수 있다.
# 지어내면 하네스가 사실과 구별하지 못하므로, 인계 탭이 그 빈칸만 묻는다
# (plans/2026-10-09-handoff-package.md §1).
#
# **룰을 해치지 않는 조건이 이 모듈의 모양을 정한다.**
#
#   - 재료가 없는 곳에만 묻는다. 문항은 두 군데서 결정적으로 나온다. 하나는 판정 결과의
#     빈 섹션이고(고정 문항), 다른 하나는 마지막 열린 결정 중 PM이 답할 것이다
#     (`OpenQuestion`). 뒤의 것이 필요한 이유: 판정은 파일의 존재로 보므로 섹션이 모두
#     sourced여도 내용의 빈칸은 남는다 — 실측(ws-g)에서 보존 기간·본인 확인
#     수준·권한처럼 구현을 막는 PM 결정 7개가 열려 있었는데 보완 질문은 0개였다. 모델이
#     질문을 새로 짓는 것이 아니라 열린 결정이 이미 "PM에게 묻는다"고 적은 줄을 그대로 되돌린다.
#     그중에서도 시점이 "빌드 전"인 줄만 묻는다. 실측(ws-g)에서 PRD가 열린 질문
#     22개 중 18개를 PM 몫으로 적었는데 구현을 막는 것은 7개였다 — 지원 외국어 목록이나 버튼
#     형태까지 인계 전에 서술형으로 받으면 PM이 넘지 못한다. "빌드 중" 줄은 "답이 나올
#     때까지" 어떻게 만들지가 적혀 있어 접어 두고, 답하고 싶으면 답할 수 있다.
#   - "PM(외부 확인)" 줄은 PM이 고르는 결정이 아니라 법무·감독기관·협력사에서 받아 올 사실이다.
#     보기를 주면 원하는 결론을 고르는 모양이 된다(실측 ws-g: "증빙 효력을 갖는가?"에 "인정한다"가
#     보기로 나왔다). 그래서 받은 답이나, 아직이면 확인 중인 곳만 받는다(`pending_with`).
#   - 기존 내용은 바꾸지 않는다. 바꾸는 길은 워크스페이스다. 여기서 받는 것은 빈칸의 답과,
#     이미 있는 AI 제안 수락 항목에 대한 "맞다" 확인뿐이다.
#   - AI 기본값을 주지 않는다. 실측에서 표시된 기본값을 PM이 고른 비율이 89%였다 —
#     기본값을 주면 이 답도 "AI 제안 수락"이 된다. 대신 "모름"을 둔다(→ 열린 질문).
#     보기는 준다(`Option`). 빈칸 서술형은 문항 하나가 작은 글쓰기 과제였다. 89%를 만든 것은
#     보기가 아니라 추천 표시였으므로, 보기는 원본에 근거가 있는 것만, 추천 없이, 모델이
#     매긴 순서를 지우고 보인다(handoff/package.parse_options).
#   - 나중에 그 섹션의 재료가 AI-PLC 산출물로 생기면 그쪽이 이긴다. 답은 지우지 않고
#     "대체됨"으로 돌려준다 — 사람이 쓴 것을 조용히 버리지 않는다.
#
# **에이전트는 읽지도 쓰지도 않는다.** 저장 키(`handoff/supplement.json`)는 프로젝트
# 접두사 안이지만 워크스페이스 밖이다. 러너가 복원하는 접두사(`aiplc-docs/`,
# `prototype/`, `uploads/`)와 정본으로 올리는 글롭(workspace_sync.SYNC_GLOBS) 어디에도
# 걸리지 않으므로 에이전트의 로컬 워크스페이스에 내려가지 않는다. 내보내기·삭제는
# 프로젝트 접두사 전체를 다루므로 함께 간다. `approvals/`, `pending/`과 같은 규율이다.
#
# **문항의 문장은 프론트가 갖는다.** 여기는 id와 섹션만 정한다 — 화면 문구는 UI 언어를
# 따르고(i18n), 패키지 합성은 id로 답을 찾는다.
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from aipds.handoff.readiness import Readiness
from aipds.s3store import S3StoreLike

_log = logging.getLogger("aipds.handoff")

#: 프로젝트 상대 키(S3Store가 `projects/{pid}/`를 붙인다).
SUPPLEMENT_KEY = "handoff/supplement.json"

#: 섹션별 보완 문항. 그 섹션이 `sourced`가 아니면 낸다. 순서가 화면 순서다.
#:
#: 섹션 2(대상)·4(사용 장면)·5(요구사항)는 묻지 않는다. 대상과 사용 장면은 Path B의
#: use case와 명세에도 있고, 요구사항의 빈칸은 PM의 서술이 아니라 build-scope(무엇을 더
#: 만들어야 하나)의 몫이다.
QUESTIONS: tuple[tuple[str, str], ...] = (
    ("problem.evidence", "problem"),
    ("problem.why_now", "problem"),
    ("goals.success", "goals"),
    ("non_goals.list", "non_goals"),
    ("constraints.rules", "constraints"),
    ("assumptions.must_be_true", "assumptions"),
    ("assumptions.failure_reasons", "assumptions"),
)
_QUESTION_IDS = frozenset(qid for qid, _ in QUESTIONS)

#: 답 하나의 상한. 화면은 여러 줄 입력이고, 이 이상은 붙여 넣은 문서다 — 그것은 업로드의 일이다.
MAX_ANSWER_CHARS = 4000
#: 확인 키: `aiplc-docs/…-questions.md#<문항 번호>`.
_CONFIRM_KEY = re.compile(r"^aiplc-docs/[^#\s]+-questions\.md#\d{1,4}$")
_MAX_CONFIRMATIONS = 500
#: 열린 질문 답의 키: `open:` + 질문 문장 해시. 다시 만들면 번호(O-03)는 바뀌므로
#: 문장으로 잇는다 — 다시 만든 판이 문장을 바꾸면 새 질문으로 보이고, 답은 기록에 남는다.
_OPEN_KEY = re.compile(r"^open:[0-9a-f]{12}$")
_MAX_OPEN_ANSWERS = 200
MAX_QUESTION_CHARS = 1000
MAX_PENDING_CHARS = 200


class Answer(BaseModel):
    text: str = Field(default="", max_length=MAX_ANSWER_CHARS)
    #: "모름". 답한 것으로 세고, 그 내용은 열린 결정으로 간다.
    unknown: bool = False
    updated_at: str = ""


class OpenAnswer(Answer):
    #: 답한 질문의 문장. 질문은 열린 결정에서 왔고 그것은 다시 만들어진다 — 기록만으로 무엇에 답했는지
    #: 알 수 있어야 다음 생성이 그 답을 쓴다.
    question: str = Field(max_length=MAX_QUESTION_CHARS)
    #: 외부 확인 질문을 지금 누구에게 확인 중인가. 답(text)이 없어도 이것만으로 답한 것으로 센다.
    pending_with: str = Field(default="", max_length=MAX_PENDING_CHARS)


class Supplement(BaseModel):
    answers: dict[str, Answer] = {}
    #: 확인 키 → 확인 시각. AI 제안 수락 항목을 PM이 "맞다"고 한 기록이다.
    confirmed: dict[str, str] = {}
    #: 열린 결정 중 PM 몫에 대한 답. 키는 `open_key(질문)`.
    open_answers: dict[str, OpenAnswer] = {}


class AnswerIn(BaseModel):
    text: str = Field(default="", max_length=MAX_ANSWER_CHARS)
    unknown: bool = False


class OpenAnswerIn(AnswerIn):
    question: str = Field(max_length=MAX_QUESTION_CHARS)
    pending_with: str = Field(default="", max_length=MAX_PENDING_CHARS)


class SupplementUpdate(BaseModel):
    """폼 전체. 저장은 교체다 — 화면이 가진 상태가 곧 기록이다."""
    answers: dict[str, AnswerIn] = {}
    confirmed: list[str] = Field(default=[], max_length=_MAX_CONFIRMATIONS)
    open_answers: dict[str, OpenAnswerIn] = Field(default={}, max_length=_MAX_OPEN_ANSWERS)


class OpenQuestion(BaseModel):
    """열린 결정에서 PM이 답할 것으로 적힌 줄 하나."""
    #: 문서 안의 번호(O-03). 다시 만들면 바뀐다 — 화면 표시용이다.
    id: str
    question: str
    #: 시점이 "빌드 전"인가. 시점 열이 없는 표(이 구분 이전)나 알 수 없는 값은 빌드 전으로
    #: 본다 — 묻지 않아서 놓치는 쪽보다 한 번 더 묻는 쪽이 싸다.
    blocking: bool = True
    #: "답이 나올 때까지" 칸. 빌드 중 결정이 그동안 어떻게 만들어지는지다.
    until: str = ""
    #: 답할 사람이 "PM(외부 확인)"이다 — 고를 결정이 아니라 받아 올 사실이다.
    external: bool = False


class Option(BaseModel):
    """빌드 전 결정 하나의 보기. 원본에 근거가 있는 것만 남는다(handoff/package.parse_options)."""
    text: str
    #: 근거 — `discovery/` 아래 상대 경로(질문 파일이면 문항 번호까지).
    source: str


class QuestionView(BaseModel):
    id: str
    section: str
    answer: Answer | None


class ConfirmationView(BaseModel):
    key: str
    file: str
    number: int
    ask: str
    answer: str
    stage: str
    #: 고른 보기의 문장 — PM이 확인하는 대상이다.
    choices: list[str]
    note: str
    remark: str
    confirmed_at: str | None


class OpenQuestionView(BaseModel):
    key: str
    #: 지금 판의 번호. 앞서 답해 열린 결정에서 빠진 질문은 비어 있다.
    id: str
    question: str
    answer: OpenAnswer | None
    until: str = ""
    external: bool = False
    #: 고를 수 있는 보기. 비어 있으면 서술형으로 묻는다. 외부 확인 질문에는 없다.
    options: list[Option] = []


class SupplementView(BaseModel):
    #: 마지막 패키지가 있는가. 없으면 열린 결정을 아직 읽을 수 없다.
    has_package: bool = False
    questions: list[QuestionView]
    #: 답이 있는데 그 섹션의 재료가 이제 AI-PLC 산출물에 있다. 그쪽이 이긴다.
    superseded: list[QuestionView]
    confirmations: list[ConfirmationView]
    #: 마지막 열린 결정 중 PM 몫이고 시점이 빌드 전인 것. 패키지가 없으면 비어 있다.
    open_questions: list[OpenQuestionView] = []
    #: PM 몫이지만 빌드 중에 정해도 되는 것. 묻지 않고 접어 둔다 — 답해도 된다.
    open_deferred: list[OpenQuestionView] = []
    #: 답했는데 지금 열린 결정에는 없는 것 — 답이 반영됐거나 질문 문장이 바뀌었다.
    #: 지우지 않는다(`superseded`와 같은 판단).
    open_answered: list[OpenQuestionView] = []


class InvalidSupplement(ValueError):
    """폼에 모르는 문항 id나 형식이 틀린 확인 키가 있다."""


def confirmation_key(file: str, number: int) -> str:
    return f"{file}#{number}"


#: 질문 끝의 관련 ID(`(R-08, C-05)`). 요구사항 번호는 다시 만들 때마다 바뀌므로 같은 질문인지
#: 볼 때는 뺀다 — 빼지 않으면 R-08이 R-09가 된 것만으로 답이 질문을 잃는다.
_ID_REF = re.compile(r"(?<![\w-])[A-Z]-\d+")
_EMPTY_PARENS = re.compile(r"[(（][\s,·、/]*[)）]")


def open_key(question: str) -> str:
    """열린 질문의 키. 문장으로 정하되 공백과 관련 ID는 보지 않는다."""
    normalized = " ".join(_EMPTY_PARENS.sub("", _ID_REF.sub("", question)).split())
    return "open:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def view(readiness: Readiness, record: Supplement,
         open_questions: list[OpenQuestion] | None = None,
         options: dict[str, list[Option]] | None = None, *,
         has_package: bool = False) -> SupplementView:
    open_sections = {s.key for s in readiness.sections if s.status in ("partial", "missing")}
    active: list[QuestionView] = []
    superseded: list[QuestionView] = []
    for qid, section in QUESTIONS:
        entry = QuestionView(id=qid, section=section, answer=record.answers.get(qid))
        if section in open_sections:
            active.append(entry)
        elif entry.answer is not None:
            superseded.append(entry)
    confirmations: list[ConfirmationView] = []
    for item in readiness.ai_defaults.items:
        key = confirmation_key(item.file, item.number)
        confirmations.append(ConfirmationView(
            key=key, file=item.file, number=item.number, ask=item.ask,
            answer=item.answer, stage=item.stage, choices=item.choices, note=item.note,
            remark=item.remark, confirmed_at=record.confirmed.get(key)))
    asked: dict[str, OpenQuestionView] = {}
    deferred: dict[str, OpenQuestionView] = {}
    for q in open_questions or []:
        key = open_key(q.question)
        if key in asked or key in deferred:
            continue
        offered = q.blocking and not q.external
        entry = OpenQuestionView(key=key, id=q.id, question=q.question, until=q.until,
                                 external=q.external, answer=record.open_answers.get(key),
                                 options=(options or {}).get(key, []) if offered else [])
        (asked if q.blocking else deferred)[key] = entry
    answered = [OpenQuestionView(key=key, id="", question=a.question, answer=a)
                for key, a in record.open_answers.items() if key not in asked and key not in deferred]
    return SupplementView(has_package=has_package, questions=active, superseded=superseded,
                          confirmations=confirmations, open_questions=list(asked.values()),
                          open_deferred=list(deferred.values()), open_answered=answered)


def apply(previous: Supplement, update: SupplementUpdate, *, now: str | None = None) -> Supplement:
    """폼을 기록으로. 바뀐 답만 시각을 새로 찍는다 — 저장 버튼을 다시 눌렀다고 모든 답이
    방금 쓴 것처럼 보이면 "언제 채웠나"가 무의미해진다."""
    unknown_ids = sorted(set(update.answers) - _QUESTION_IDS)
    if unknown_ids:
        raise InvalidSupplement(f"unknown question ids: {unknown_ids}")
    bad_keys = sorted(k for k in update.confirmed if not _CONFIRM_KEY.match(k))
    if bad_keys:
        raise InvalidSupplement(f"malformed confirmation keys: {bad_keys}")
    # 키는 질문 문장에서 나온다. 맞지 않으면 다른 질문의 답을 덮어쓸 수 있다.
    bad_open = sorted(k for k, a in update.open_answers.items()
                      if not _OPEN_KEY.match(k) or open_key(a.question) != k)
    if bad_open:
        raise InvalidSupplement(f"malformed open-question keys: {bad_open}")
    stamp = now or _now()
    answers: dict[str, Answer] = {}
    for qid, given in update.answers.items():
        if (kept := _kept(previous.answers.get(qid), given, stamp)) is not None:
            answers[qid] = Answer(**kept)
    open_answers: dict[str, OpenAnswer] = {}
    for key, given in update.open_answers.items():
        pending = " ".join(given.pending_with.split())
        if (kept := _kept(previous.open_answers.get(key), given, stamp, pending)) is not None:
            open_answers[key] = OpenAnswer(question=" ".join(given.question.split()), **kept)
    confirmed = {key: previous.confirmed.get(key, stamp)
                 for key in dict.fromkeys(update.confirmed)}
    return Supplement(answers=answers, confirmed=confirmed, open_answers=open_answers)


def _kept(old: Answer | None, given: AnswerIn, stamp: str,
          pending_with: str | None = None) -> dict | None:
    """답 하나를 기록의 필드로. 비운 칸은 답이 아니다(None). `pending_with`는 열린 질문만 갖는다."""
    text = given.text.strip()
    if not text and not given.unknown and not pending_with:
        return None
    same = (old is not None and old.text == text and old.unknown == given.unknown
            and getattr(old, "pending_with", "") == (pending_with or ""))
    kept = {"text": text, "unknown": given.unknown,
            "updated_at": old.updated_at if same and old else stamp}
    if pending_with is not None:
        kept["pending_with"] = pending_with
    return kept


async def load(s3: S3StoreLike) -> Supplement:
    """없으면 빈 기록. 손상된 기록은 빈 기록으로 읽고 경고한다 — 화면을 막지 않는다
    (approval_store가 손상 레코드를 건너뛰는 것과 같은 판단)."""
    try:
        raw = await s3.get(SUPPLEMENT_KEY)
    except FileNotFoundError:
        return Supplement()
    try:
        return Supplement.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValueError):
        _log.warning("handoff supplement record is unreadable — treating as empty")
        return Supplement()


async def save(s3: S3StoreLike, record: Supplement) -> None:
    await s3.put(SUPPLEMENT_KEY, record.model_dump_json())


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
