# backend/aipds/agent/answer_store.py — S3 records of submitted answers.
#
# Why this is needed: history restore was depending on **prose the CLI rewrote in English**.
# The tool_result of the SDK's AskUserQuestion is a fixed sentence the CLI produces:
#
#   Your questions have been answered: "question"="option label", ... . You can now …
#
# session_history._cli_answer_summary cannot unpack answers from this sentence — it is not
# JSON, so the result is a single line `답변 제출: <English sentence>`, and the question numbers, option letters,
# and option text all disappear.
# The live screen holds the question payload and the answer dict in hand and draws with answerSummary(), so
# the same conversation looked different before and after a refresh.
#
# Instead of re-parsing the prose, **we record the exact values at the moment the answers arrive.** The approval
# gate already made the same decision (08aaa85 "승인을 레코드로 기록 — 산문 파싱
# 의존을 끊는다" (record approvals as records — cut the dependency on prose parsing)). That sentence is not ours but the CLI's,
# and when question text contains quotes (observed: `"내부 도구라면 \"왜 …를 사서 쓰지 않고 만드는가\"가 …"`)
# parsing becomes ambiguous in principle.
#
# The key is the **tool_use_id**. The SDK gives that value to the can_use_tool callback (non-empty is a
# protocol guarantee) and the transcript's tool_result carries the same id, so the join on restore
# is exact — no need to match by order or timestamp. There is one object per round, and a
# resubmission overwrites the same key (consistent with a round having one final answer).
from __future__ import annotations

import asyncio
import json
import logging

from aipds.s3store import S3StoreLike, get_by_etag, list_with_etags

_log = logging.getLogger("aipds.agent")

ANSWERS_PREFIX = "answers/"


def _key(tool_use_id: str, prefix: str) -> str:
    return f"{prefix}{tool_use_id}.json"


def _is_valid(data: dict) -> bool:
    """Blocks only top-level types that would break restore right away (same discipline as pending_store).

    answers must be a string map of {question number: value} — the frontend's
    Record<string, string> contract, and answerSummary interprets those values as letters.
    """
    answers = data.get("answers")
    return (
        isinstance(data.get("tool_use_id"), str) and data["tool_use_id"] != "" and
        isinstance(data.get("questions"), dict) and
        isinstance(answers, dict) and
        all(isinstance(k, str) and isinstance(v, str) for k, v in answers.items())
    )


async def save_answers(s3: S3StoreLike, *, tool_use_id: str, interrupt_id: str,
                       questions: dict, answers: dict[str, str],
                       prefix: str = ANSWERS_PREFIX) -> None:
    """Records one round's question payload + answers.

    Storing questions together is load-bearing: answer values are letters ("A",
    "B,C", "A: 부연"), and expanding them into option text requires the payload from that moment.
    The SDK original also remains in the transcript's tool_use.input, but reassembling from it
    would make letter assignment depend on question_file_from_sdk's behavior at that time —
    keeping the letters the user actually saw is more accurate.

    `prefix` is who owns the record. Discovery puts it in `answers/` at the project root,
    and prototype builds in `prototypes/{slug}/history/answers/`
    (proto/history.py) — prototype reset deletes everything under that prefix, so records that must
    disappear along with the conversation have to live under it.
    """
    await s3.put(_key(tool_use_id, prefix), json.dumps({
        "tool_use_id": tool_use_id,
        "interrupt_id": interrupt_id,
        "questions": questions,
        "answers": answers,
    }, ensure_ascii=False))


async def load_answers(s3: S3StoreLike,
                       prefix: str = ANSWERS_PREFIX) -> dict[str, dict]:
    """tool_use_id → record. Any failure skips only that record.

    History is auxiliary data (same principle as list_history's downgrade), and one corrupted entry
    must not block restoring the remaining rounds. If the listing itself fails, an empty dict —
    callers fall into the same path as old sessions without records (current wording kept).
    """
    try:
        listed = await list_with_etags(s3, prefix)
    except Exception:
        _log.exception("answer record listing failed")
        return {}
    keys = [k for k, _ in listed]
    # **Parallel GET.** It is linear in the number of rounds, so reading sequentially delays history
    # loading by that much (observed 2026-08-17: 30ms per S3 round trip). The same judgment as
    # session_store.load_transcript, and project_store.load_manifest is this repo's precedent.
    # It uses the same ETag cache as transcripts — records only grow by one per round.
    bodies = await asyncio.gather(*(get_by_etag(s3, k, etag) for k, etag in listed),
                                 return_exceptions=True)
    out: dict[str, dict] = {}
    for key, body in zip(keys, bodies):
        try:
            if isinstance(body, BaseException):
                raise body
            data = json.loads(body)
        except Exception:
            _log.warning("unreadable answer record skipped: %s", key)
            continue
        if not isinstance(data, dict) or not _is_valid(data):
            _log.warning("malformed answer record skipped: %s", key)
            continue
        out[data["tool_use_id"]] = data
    return out
