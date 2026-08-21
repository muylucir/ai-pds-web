# backend/aipds/agent/pending_store.py -- S3 persistence for the pending question.
#
# Why it is needed: Strands persisted the pending interrupt into the session (so
# reading agent._interrupt_state was enough), but the Claude Agent SDK's session
# store is a transcript mirror, which leaves the pending question as an in-memory
# Future. GET /pending -- restoring the question form after a refresh -- depends on
# that state, so it is stored separately.
#
# A project has at most one pending question, so the key is fixed (S3Store prepends
# the project prefix).
from __future__ import annotations

import json
import logging

from aipds.s3store import S3StoreLike

_log = logging.getLogger("aipds.agent")

PENDING_KEY = "pending/questions.json"


def _is_valid(data: dict) -> bool:
    """Field presence alone is not enough: a wrong type (say sdk_questions arriving as
    a string) blows up later in Task 6's answer back-translation path. The internal
    structure of questions/sdk_questions is not validated here (that is the parser's
    and the builder's job); this guards only the top-level types whose absence breaks
    answer back-translation and the session lookup immediately."""
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
    """Also store sdk_questions (the SDK's raw form): it is needed to translate
    answers back into SDK labels, and after a restart there is no in-memory copy."""
    await s3.put(PENDING_KEY, json.dumps({
        "interrupt_id": interrupt_id,
        "questions": questions,
        "sdk_questions": sdk_questions,
        "session_id": session_id,
    }, ensure_ascii=False))


async def load_pending(s3: S3StoreLike) -> dict | None:
    """None when absent or corrupt. It does not raise a 500: pending is a convenience
    for restore, and without it the user can start the turn again. Restoring halfway
    would be worse."""
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
    """Idempotent -- an answer submission and an interrupt can overlap and call this
    twice."""
    await s3.delete_prefix(PENDING_KEY)


#: The path of the **open file** in a file question round. It is a separate key
#: from PENDING_KEY above because what it holds is entirely different -- loosening
#: `_is_valid` to cover both would weaken the validation of the AskUserQuestion path,
#: which is still live.
PENDING_FILE_KEY = "pending/question-file.json"


async def save_pending_file(s3: S3StoreLike, *, file: str) -> None:
    """Store only the path of the open question file.

    **The question content is not stored.** Re-reading the file every time buys
    three things:

    1. It is always current -- if the agent edits the file, the card follows.
    2. **There is no clear step.** `runner.write_file` writes straight to S3
       (runner.py:57-59), so S3 is current the moment the answers are recorded, and
       with no unanswered questions the restore naturally returns None. There is
       structurally no path where forgetting to clear leaves a dead card behind --
       that is what differs from the old path, where `disconnect` had to chase dead
       questions out of three places.
    3. No fuzzy matching is needed. The file is the source of record and the number
       is the key.

    So why store the path at all -- **because of ambiguity.** One measured project
    had three unanswered question files at once (the result of lost answers). A scan
    alone cannot tell which round is open, and showing the wrong card is worse than
    showing none.
    """
    await s3.put(PENDING_FILE_KEY, json.dumps({"file": file},
                                              ensure_ascii=False))


async def load_pending_file(s3: S3StoreLike) -> str | None:
    """The path of the open question file, or None when absent or corrupt.

    It does not raise a 500 for the same reason as `load_pending`: restore is a
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
