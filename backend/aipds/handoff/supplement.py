# backend/aipds/handoff/supplement.py — supplementary answers collected from the PM at handoff time.
#
# **Why this exists.** Depending on the starting point, material for some topics is missing (handoff/readiness). Path B and EP1
# skip Envision, so the problem's evidence, assumptions, and non-goals are empty, and only the PM can answer those.
# If we made them up, the harness could not tell them apart from facts, so the handoff tab asks only about those gaps
# (plans/2026-10-09-handoff-package.md §1).
#
# **The conditions for not undermining the rules determine the shape of this module.**
#
#   - Ask only where material is missing. Questions come deterministically from two places. One is the empty
#     sections in the assessment result (fixed questions); the other is the last open decisions that the PM is to answer
#     (`OpenQuestion`). Why the latter is needed: the assessment looks at file existence, so even when every section is
#     sourced, gaps in the content remain — in an observed case (ws-g), 7 PM decisions that blocked implementation, such as
#     retention period, identity-verification level, and permissions, were open, yet there were 0 supplementary questions. The model
#     does not compose new questions; it hands back verbatim the lines where the open decisions already say "ask the PM".
#     Of those, only lines whose timing is "빌드 전" (before build) are asked. In an observed case (ws-g), the PRD assigned 18 of 22
#     open questions to the PM, but only 7 blocked implementation — if even the list of supported languages or button
#     shapes is collected as free text before handoff, the PM cannot get past it. "빌드 중" (during build) lines state how to build
#     "답이 나올 때까지" (until an answer arrives), so they are folded away; they can still be answered if the PM wants.
#   - "PM(외부 확인)" lines are not decisions the PM chooses but facts to be obtained from legal, regulators, or partners.
#     Offering choices turns it into picking the desired conclusion (observed ws-g: for "증빙 효력을 갖는가?", "인정한다"
#     appeared as a choice). So we take only the answer received or, if not yet, who it is being confirmed with (`pending_with`).
#   - Existing content is not changed. The way to change it is the workspace. All that is collected here is answers to gaps
#     and a "that's right" confirmation for existing "AI 제안 수락" (AI suggestion accepted) items.
#   - No AI defaults are offered. In observed data, the PM picked the displayed default 89% of the time —
#     offering a default would make this answer an "AI 제안 수락" too. Instead there is "모름" (unknown) (→ open question).
#     Choices are offered (`Option`). With blank free-text, each question was a small writing assignment. What produced the 89%
#     was the recommendation marker, not the choices, so choices are shown only when grounded in the source, without a
#     recommendation, and with the model's ordering erased (handoff/package.parse_options).
#   - If material for that section later appears as an AI-PLC artifact, that side wins. The answer is not deleted but
#     returned as "superseded" — we do not silently throw away what a person wrote.
#
# **The agent neither reads nor writes it.** The storage key (`handoff/supplement.json`) is inside the project
# prefix but outside the workspace. It matches none of the prefixes the runner restores (`aiplc-docs/`,
# `prototype/`, `uploads/`) nor the globs uploaded as the source of truth (workspace_sync.SYNC_GLOBS), so it never
# lands in the agent's local workspace. Export and delete handle the whole project prefix, so it goes along with them.
# Same discipline as `approvals/` and `pending/`.
#
# **The frontend owns the question wording.** This module only defines ids and sections — on-screen text follows the UI
# language (i18n), and package composition finds answers by id.
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

#: Project-relative key (S3Store prepends `projects/{pid}/`).
SUPPLEMENT_KEY = "handoff/supplement.json"

#: Supplementary questions per section. Asked when that section is not `sourced`. The order is the on-screen order.
#:
#: Sections 2 (audience), 4 (usage scenarios), and 5 (requirements) are not asked. Audience and usage scenarios also exist in Path B's
#: use cases and spec, and gaps in requirements belong to build-scope (what else needs to be built), not
#: to the PM's narrative.
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

#: Upper bound for one answer. The screen is a multi-line input; anything beyond this is a pasted document — that is the job of uploads.
MAX_ANSWER_CHARS = 4000
#: Confirmation key: `aiplc-docs/…-questions.md#<question number>`.
_CONFIRM_KEY = re.compile(r"^aiplc-docs/[^#\s]+-questions\.md#\d{1,4}$")
_MAX_CONFIRMATIONS = 500
#: Key for an open-question answer: `open:` + hash of the question sentence. Regeneration changes the number (O-03), so we
#: link by sentence — if a regenerated version changes the sentence, it appears as a new question and the answer stays in the record.
_OPEN_KEY = re.compile(r"^open:[0-9a-f]{12}$")
_MAX_OPEN_ANSWERS = 200
MAX_QUESTION_CHARS = 1000
MAX_PENDING_CHARS = 200


class Answer(BaseModel):
    text: str = Field(default="", max_length=MAX_ANSWER_CHARS)
    #: "모름" (unknown). Counts as answered, and its content goes to the open decisions.
    unknown: bool = False
    updated_at: str = ""


class OpenAnswer(Answer):
    #: The sentence of the question answered. Questions come from open decisions, which get regenerated — the record alone must
    #: tell what was answered so the next generation can use that answer.
    question: str = Field(max_length=MAX_QUESTION_CHARS)
    #: Who an external-confirmation question is currently being confirmed with. Counts as answered on its own, even without an answer (text).
    pending_with: str = Field(default="", max_length=MAX_PENDING_CHARS)


class Supplement(BaseModel):
    answers: dict[str, Answer] = {}
    #: Confirmation key → confirmation time. A record that the PM said "that's right" to an "AI 제안 수락" item.
    confirmed: dict[str, str] = {}
    #: Answers to the PM's share of open decisions. Keyed by `open_key(question)`.
    open_answers: dict[str, OpenAnswer] = {}


class AnswerIn(BaseModel):
    text: str = Field(default="", max_length=MAX_ANSWER_CHARS)
    unknown: bool = False


class OpenAnswerIn(AnswerIn):
    question: str = Field(max_length=MAX_QUESTION_CHARS)
    pending_with: str = Field(default="", max_length=MAX_PENDING_CHARS)


class SupplementUpdate(BaseModel):
    """The whole form. Saving replaces — the state the screen holds is the record."""
    answers: dict[str, AnswerIn] = {}
    confirmed: list[str] = Field(default=[], max_length=_MAX_CONFIRMATIONS)
    open_answers: dict[str, OpenAnswerIn] = Field(default={}, max_length=_MAX_OPEN_ANSWERS)


class OpenQuestion(BaseModel):
    """One line in the open decisions marked as for the PM to answer."""
    #: The number within the document (O-03). Changes on regeneration — for display only.
    id: str
    question: str
    #: Whether the timing is "빌드 전" (before build). Tables without a timing column (predating this distinction) or unknown values are
    #: treated as before build — asking once more is cheaper than missing one by not asking.
    blocking: bool = True
    #: The "답이 나올 때까지" (until an answer arrives) cell. How a during-build decision is handled in the meantime.
    until: str = ""
    #: The person to answer is "PM(외부 확인)" (PM, external confirmation) — a fact to obtain, not a decision to choose.
    external: bool = False


class Option(BaseModel):
    """A choice for one before-build decision. Only those grounded in the source remain (handoff/package.parse_options)."""
    text: str
    #: Evidence — a relative path under `discovery/` (including the question number for a question file).
    source: str


class Citation(BaseModel):
    """A vision.md line that relies on an accepted AI suggestion it marks as unconfirmed."""
    #: The gist of that line.
    text: str
    #: The line is in the first-release scope or the success metrics — what development builds toward.
    scope: bool = False


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
    #: The sentence of the chosen choice — what the PM confirms.
    choices: list[str]
    note: str
    remark: str
    confirmed_at: str | None
    #: The vision.md lines that rely on this decision as an unconfirmed AI suggestion. Empty if vision.md does not (or there is
    #: no package yet).
    in_vision: list[str] = []
    #: One of those lines is in the first-release scope or the success metrics. Observed (ws-o): those targets went to development
    #: as unconfirmed AI suggestions, and the development harness re-asked the scope but not them.
    in_scope: bool = False


class OpenQuestionView(BaseModel):
    key: str
    #: The number in the current version. Empty for a question that was answered earlier and dropped from the open decisions.
    id: str
    question: str
    answer: OpenAnswer | None
    until: str = ""
    external: bool = False
    #: Selectable choices. If empty, asked as free text. None for external-confirmation questions.
    options: list[Option] = []


class SupplementView(BaseModel):
    #: Whether a last package exists. Without one, the open decisions cannot be read yet.
    has_package: bool = False
    questions: list[QuestionView]
    #: Has an answer, but material for that section now exists in AI-PLC artifacts. That side wins.
    superseded: list[QuestionView]
    confirmations: list[ConfirmationView]
    #: The PM's share of the last open decisions whose timing is before build. Empty if there is no package.
    open_questions: list[OpenQuestionView] = []
    #: The PM's share, but fine to decide during the build. Folded away without asking — may still be answered.
    open_deferred: list[OpenQuestionView] = []
    #: Answered but not in the current open decisions — the answer was applied or the question sentence changed.
    #: Not deleted (same judgment as `superseded`).
    open_answered: list[OpenQuestionView] = []


class InvalidSupplement(ValueError):
    """The form has an unknown question id or a malformed confirmation key."""


def confirmation_key(file: str, number: int) -> str:
    return f"{file}#{number}"


#: Related IDs at the end of a question (`(R-08, C-05)`). Requirement numbers change on every regeneration, so they are
#: removed when checking whether it is the same question — otherwise the answer loses its question just because R-08 became R-09.
_ID_REF = re.compile(r"(?<![\w-])[A-Z]-\d+")
_EMPTY_PARENS = re.compile(r"[(（][\s,·、/]*[)）]")


def open_key(question: str) -> str:
    """Key of an open question. Based on the sentence, ignoring whitespace and related IDs."""
    normalized = " ".join(_EMPTY_PARENS.sub("", _ID_REF.sub("", question)).split())
    return "open:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def view(readiness: Readiness, record: Supplement,
         open_questions: list[OpenQuestion] | None = None,
         options: dict[str, list[Option]] | None = None, *,
         has_package: bool = False,
         cited: dict[str, list[Citation]] | None = None) -> SupplementView:
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
        citations = (cited or {}).get(key, [])
        confirmations.append(ConfirmationView(
            key=key, file=item.file, number=item.number, ask=item.ask,
            answer=item.answer, stage=item.stage, choices=item.choices, note=item.note,
            remark=item.remark, confirmed_at=record.confirmed.get(key),
            in_vision=[c.text for c in citations], in_scope=any(c.scope for c in citations)))
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
    """Form to record. Only changed answers get a new timestamp — if pressing save again made every answer
    look freshly written, "when was it filled in" would become meaningless."""
    unknown_ids = sorted(set(update.answers) - _QUESTION_IDS)
    if unknown_ids:
        raise InvalidSupplement(f"unknown question ids: {unknown_ids}")
    bad_keys = sorted(k for k in update.confirmed if not _CONFIRM_KEY.match(k))
    if bad_keys:
        raise InvalidSupplement(f"malformed confirmation keys: {bad_keys}")
    # The key comes from the question sentence. If it doesn't match, it could overwrite another question's answer.
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
    """One answer to record fields. A cleared cell is not an answer (None). Only open questions have `pending_with`."""
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
    """Empty record if absent. A corrupt record is read as empty with a warning — it does not block the screen
    (same judgment as approval_store skipping corrupt records)."""
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
