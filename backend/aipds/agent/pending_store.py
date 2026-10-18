# backend/aipds/agent/pending_store.py — S3 persistence for pending questions.
#
# Why it is needed: Strands persisted the pending interrupt along with the session
# (so reading agent._interrupt_state was enough), but the Claude Agent SDK's session
# store is a transcript mirror, so a pending question is an in-memory Future.
# GET /pending -- restoring the question form after a refresh -- relies on that, so
# it is stored separately.
#
# A project has only one pending question, so the key is fixed (S3Store adds the
# project prefix).
from __future__ import annotations

import json
import logging

from aipds.s3store import S3StoreLike

_log = logging.getLogger("aipds.agent")

PENDING_KEY = "pending/questions.json"


def _is_valid(data: dict) -> bool:
    """Field presence alone is not enough -- if a type is wrong (e.g. sdk_questions is a
    string), it blows up later in Task 6's answer back-translation path. The internal
    structure of questions/sdk_questions is not validated (that is the parser's/builder's
    job); this only guards the top-level types whose breakage would immediately break
    answer back-translation and session lookup."""
    interrupt_id = data.get("interrupt_id")
    session_id = data.get("session_id")
    return (
        isinstance(interrupt_id, str) and interrupt_id != "" and
        isinstance(data.get("questions"), dict) and
        isinstance(data.get("sdk_questions"), list) and
        isinstance(session_id, str) and session_id != ""
    )


async def save_pending(s3: S3StoreLike, *, interrupt_id: str, questions: dict,
                       sdk_questions: list[dict], session_id: str) -> None:
    """Stores sdk_questions (the SDK's original form) too -- needed to back-translate
    answers into SDK labels, and after a restart there is no in-memory copy."""
    await s3.put(PENDING_KEY, json.dumps({
        "interrupt_id": interrupt_id,
        "questions": questions,
        "sdk_questions": sdk_questions,
        "session_id": session_id,
    }, ensure_ascii=False))


async def load_pending(s3: S3StoreLike) -> dict | None:
    """None if missing or corrupted. Does not raise a 500 -- pending is a restore
    convenience, and without it the user can start the turn again. A half-restore is worse."""
    try:
        raw = await s3.get(PENDING_KEY)
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        _log.warning("pending payload is not valid JSON — ignoring")
        return None
    if not isinstance(data, dict) or not _is_valid(data):
        _log.warning("pending payload missing or malformed required fields — ignoring")
        return None
    return data


async def clear_pending(s3: S3StoreLike) -> None:
    """Idempotent -- an answer submission and an interrupt can overlap and call this twice."""
    await s3.delete_prefix(PENDING_KEY)


#: Path of the **open file** in a question-file round. It is a separate key from
#: PENDING_KEY above because what it holds is entirely different -- loosening
#: `_is_valid` would also weaken validation for the still-live AskUserQuestion path.
PENDING_FILE_KEY = "pending/question-file.json"


async def save_pending_file(s3: S3StoreLike, *, file: str) -> None:
    """Stores only the path of the open question file.

    **The question content is not stored.** Re-reading from the file every time gives
    three things:

    1. It is always current -- if the agent edits the file, the card follows.
    2. **There is no clear step.** `runner.write_file` writes to S3 directly
       (runner.py:57-59), so S3 is current the moment an answer is recorded, and with
       no unanswered items the restore naturally becomes None. There is structurally
       no path where a dead card remains because someone forgot to delete it -- this
       is what differs from the old path, where `disconnect` had to evict dead
       questions in three places.
    3. No fuzzy matching is needed. The file is the source of truth and the number is
       the key.

    Then why store the path -- **because of ambiguity.** An observed project had 3
    unanswered question files at once (the result of lost answers). A scan alone
    cannot tell which round is open, and showing the wrong card is worse than
    showing none.
    """
    await s3.put(PENDING_FILE_KEY, json.dumps({"file": file},
                                              ensure_ascii=False))


async def load_pending_file(s3: S3StoreLike) -> str | None:
    """Path of the open question file. None if missing or corrupted.

    It does not raise a 500 for the same reason as `load_pending` -- restoring is a
    convenience, and without it the user can start the turn again."""
    try:
        raw = await s3.get(PENDING_FILE_KEY)
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        _log.warning("pending question-file payload is not valid JSON — ignoring")
        return None
    file = data.get("file") if isinstance(data, dict) else None
    if not isinstance(file, str) or not file:
        _log.warning("pending question-file payload has no usable path — ignoring")
        return None
    return file
