# backend/aipds/agent/answer_store.py -- the S3 record of submitted answers.
#
# Why it is needed: history restore used to depend on **prose the CLI wrote in
# English**. The tool_result of the SDK's AskUserQuestion is a fixed sentence the
# CLI composes:
#
#   Your questions have been answered: "question"="option label", ... . You can now …
#
# session_history._cli_answer_summary cannot expand answers out of that sentence --
# it is not JSON, so the result was a single line reading "answers submitted:
# <English sentence>", losing the question numbers, the option letters and the
# option texts alike. The live screen has the question payload and the answers dict
# in hand and draws them with answerSummary(), so the same conversation looked
# different before and after a refresh.
#
# Rather than re-parse that prose, **record the exact values at the moment the
# answers arrive.** The same decision was already made for the approval gate
# (08aaa85, "record approvals as records -- cut the dependency on parsing prose").
# That sentence is the CLI's, not ours, and once a question text contains quotes
# (measured: `"for an internal tool, \"why build rather than buy …\" is …"`) the
# parsing becomes ambiguous in principle.
#
# The key is the **tool_use_id**. The SDK gives that value to the can_use_tool
# callback (non-empty is a protocol guarantee) and the transcript's tool_result
# carries the same id, so the join at restore time is exact -- no matching by order
# or timestamp. One object per round, and a resubmission overwrites the same key
# (consistent with a round having exactly one final set of answers).
from __future__ import annotations

import asyncio
import json
import logging

from aipds.s3store import S3StoreLike

_log = logging.getLogger("aipds.agent")

ANSWERS_PREFIX = "answers/"


def _key(tool_use_id: str) -> str:
    return f"{ANSWERS_PREFIX}{tool_use_id}.json"


def _is_valid(data: dict) -> bool:
    """Guard only the top-level types whose absence breaks restore immediately (the
    same discipline as pending_store).

    `answers` has to be a string map of {question number: value} -- the frontend's
    Record<string, string> contract, whose values answerSummary interprets as option
    letters.
    """
    answers = data.get("answers")
    return (
        isinstance(data.get("tool_use_id"), str) and data["tool_use_id"] != "" and
        isinstance(data.get("questions"), dict) and
        isinstance(answers, dict) and
        all(isinstance(k, str) and isinstance(v, str) for k, v in answers.items())
    )


async def save_answers(s3: S3StoreLike, *, tool_use_id: str, interrupt_id: str,
                       questions: dict, answers: dict[str, str]) -> None:
    """Record one round's question payload together with its answers.

    Storing `questions` alongside them is load-bearing: the answer values are
    letters ("A", "B,C", "A: an addendum"), and expanding those into option texts
    needs the payload as it was at that moment. The transcript's tool_use.input does
    keep the SDK's raw form, but reassembling from it would make the letter
    assignment depend on question_file_from_sdk's behaviour at that point in time --
    keeping the letters the user actually saw is more accurate.
    """
    await s3.put(_key(tool_use_id), json.dumps({
        "tool_use_id": tool_use_id,
        "interrupt_id": interrupt_id,
        "questions": questions,
        "answers": answers,
    }, ensure_ascii=False))


async def load_answers(s3: S3StoreLike) -> dict[str, dict]:
    """tool_use_id -> record. Any failure skips only that record.

    History is auxiliary data (the same principle as list_history's degradation) and
    one corrupt entry must not block the other rounds from restoring. A failure of
    the listing itself gives an empty dict, and the caller falls back to the same
    path as an older session with no records (keeping the current wording).
    """
    try:
        keys = await s3.list(ANSWERS_PREFIX)
    except Exception:
        _log.exception("answer record listing failed")
        return {}
    # **Parallel GETs.** This scales linearly with the number of rounds, so
    # reading sequentially slows history loading by exactly that factor (measured
    # 2026-08-17: 30ms per S3 round trip). The same judgement as
    # session_store.load_transcript, with project_store.load_manifest as this repo's
    # precedent.
    bodies = await asyncio.gather(*(s3.get(k) for k in keys),
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
