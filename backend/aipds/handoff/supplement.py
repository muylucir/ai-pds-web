# backend/aipds/handoff/supplement.py — 인계 시점에 PM에게 받는 보완 답.
#
# **왜 있는가.** 출발점에 따라 PRD 섹션의 재료가 없다(handoff/readiness). Path B와 EP1은
# Envision을 거치지 않아 문제의 근거·가정·하지 않을 것이 비고, 그것은 PM만 답할 수 있다.
# 지어내면 하네스가 사실과 구별하지 못하므로, 인계 탭이 그 빈칸만 묻는다
# (plans/2026-10-09-handoff-package.md §1).
#
# **룰을 해치지 않는 조건이 이 모듈의 모양을 정한다.**
#
#   - 재료가 없는 섹션에만 묻는다. 문항은 판정 결과에서 결정적으로 나온다 — 모델이
#     질문을 지어 섹션을 늘리면 웹이 두 번째 Envision이 된다.
#   - 기존 내용은 바꾸지 않는다. 바꾸는 길은 워크스페이스다. 여기서 받는 것은 빈칸의 답과,
#     이미 있는 AI 제안 수락 항목에 대한 "맞다" 확인뿐이다.
#   - AI 기본값을 주지 않는다. 실측에서 표시된 기본값을 PM이 고른 비율이 89%였다 —
#     기본값을 주면 이 답도 "AI 제안 수락"이 된다. 대신 "모름"을 둔다(→ 열린 질문).
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


class Answer(BaseModel):
    text: str = Field(default="", max_length=MAX_ANSWER_CHARS)
    #: "모름". 답한 것으로 세고, 그 내용은 PRD의 열린 질문으로 간다.
    unknown: bool = False
    updated_at: str = ""


class Supplement(BaseModel):
    answers: dict[str, Answer] = {}
    #: 확인 키 → 확인 시각. AI 제안 수락 항목을 PM이 "맞다"고 한 기록이다.
    confirmed: dict[str, str] = {}


class AnswerIn(BaseModel):
    text: str = Field(default="", max_length=MAX_ANSWER_CHARS)
    unknown: bool = False


class SupplementUpdate(BaseModel):
    """폼 전체. 저장은 교체다 — 화면이 가진 상태가 곧 기록이다."""
    answers: dict[str, AnswerIn] = {}
    confirmed: list[str] = Field(default=[], max_length=_MAX_CONFIRMATIONS)


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


class SupplementView(BaseModel):
    questions: list[QuestionView]
    #: 답이 있는데 그 섹션의 재료가 이제 AI-PLC 산출물에 있다. 그쪽이 이긴다.
    superseded: list[QuestionView]
    confirmations: list[ConfirmationView]


class InvalidSupplement(ValueError):
    """폼에 모르는 문항 id나 형식이 틀린 확인 키가 있다."""


def confirmation_key(file: str, number: int) -> str:
    return f"{file}#{number}"


def view(readiness: Readiness, record: Supplement) -> SupplementView:
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
    return SupplementView(questions=active, superseded=superseded,
                          confirmations=confirmations)


def apply(previous: Supplement, update: SupplementUpdate, *, now: str | None = None) -> Supplement:
    """폼을 기록으로. 바뀐 답만 시각을 새로 찍는다 — 저장 버튼을 다시 눌렀다고 모든 답이
    방금 쓴 것처럼 보이면 "언제 채웠나"가 무의미해진다."""
    unknown_ids = sorted(set(update.answers) - _QUESTION_IDS)
    if unknown_ids:
        raise InvalidSupplement(f"unknown question ids: {unknown_ids}")
    bad_keys = sorted(k for k in update.confirmed if not _CONFIRM_KEY.match(k))
    if bad_keys:
        raise InvalidSupplement(f"malformed confirmation keys: {bad_keys}")
    stamp = now or _now()
    answers: dict[str, Answer] = {}
    for qid, given in update.answers.items():
        text = given.text.strip()
        if not text and not given.unknown:
            continue  # 비운 칸은 답이 아니다
        old = previous.answers.get(qid)
        same = old is not None and old.text == text and old.unknown == given.unknown
        answers[qid] = Answer(text=text, unknown=given.unknown,
                              updated_at=old.updated_at if same and old else stamp)
    confirmed = {key: previous.confirmed.get(key, stamp)
                 for key in dict.fromkeys(update.confirmed)}
    return Supplement(answers=answers, confirmed=confirmed)


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
