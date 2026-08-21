# backend/aipds/approval_store.py -- the structured record of an approval decision.
#
# **Why this file exists.** Approval was being recovered by regex from the prose of `audit.md`.
# The fact that the user pressed the gate button is known with certainty at that moment, and
# this was a structure that threw it away and then recovered what the agent had transcribed
# into natural language.
#
# Measured (41 entries in pilot1's audit.md): only 2 of 5 approval gates were recognised.
# Because the user answers in the chat -- "승인" was recognised, but "동의" (a final
# approval!), "진행" and a multiple-choice "A" all failed. On top of that, a normal
# progress description containing 'update' made the invalidation logic erase even the
# approvals that had been recognised. All that was left on screen was "there is no recorded
# approval history".
#
# The same symptom was fixed three times (ca8c508 the parser, 68e143f the display condition,
# e18d681 the language). Every one of them was "how do we read the agent's output", and that is
# a value we do not control. So the basis for the decision moves to **a value we write
# ourselves**. This record is the primary basis, and audit log parsing is demoted to a fallback
# for existing projects that have no records.
#
# **It does not replace audit.md.** The human-readable audit trail continues to be written by
# the agent per the upstream rules (rule/ is data -- we do not edit it). This record is a copy
# for machine adjudication, and since the two have different roles both remain.
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass

from aipds.s3store import S3StoreLike

_log = logging.getLogger("aipds.approval")

#: A project-relative path, since S3Store prepends projects/{pid}/ (the same discipline as
#: pending_store). Listing by this prefix yields the project's whole approval history.
APPROVALS_PREFIX = "approvals/"


@dataclass(frozen=True)
class ApprovalRecord:
    """One approval decision.

    doc_hash is the crux of the invalidation logic. It used to search the audit log's prose for
    `수정|update|갱신` to guess "the document changed, so re-approval is needed", and a normal
    progress description commonly contains those words, producing false positives (measured:
    idx=40). Recording the document's hash at approval time makes it possible to decide **as a
    matter of fact** by comparing against the current document.
    """
    document: str
    doc_hash: str
    approved_at: str


def _key(approved_at: str) -> str:
    """A timestamp plus a random suffix. Without the suffix, two approvals in the same second
    (a rapid re-approval) would have the later overwrite the earlier and the audit record would
    disappear. Colons are awkward in an S3 key, so they become '-'."""
    stamp = approved_at.replace(":", "-")
    return f"{APPROVALS_PREFIX}{stamp}-{uuid.uuid4().hex[:8]}.json"


async def save_approval(s3: S3StoreLike, *, document: str, doc_hash: str,
                        approved_at: str) -> None:
    """Record an approval. It accumulates rather than overwriting -- an approval is a history."""
    await s3.put(_key(approved_at), json.dumps({
        "document": document,
        "doc_hash": doc_hash,
        "approved_at": approved_at,
    }, ensure_ascii=False))


def _parse(raw: str) -> ApprovalRecord | None:
    """One corrupted entry does not lose the whole history (the same judgement by which
    pending_store returns None for a corrupted payload)."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        _log.warning("approval record is not valid JSON — skipping")
        return None
    if not isinstance(data, dict):
        return None
    document, doc_hash = data.get("document"), data.get("doc_hash")
    approved_at = data.get("approved_at")
    # A missing field would blow up later in the hash comparison -- it is filtered out at the
    # point of reading. Each value is narrowed separately rather than with `all(...)` because a
    # type checker cannot propagate an isinstance inside a generator to the outside.
    if not (isinstance(document, str) and document
            and isinstance(doc_hash, str) and doc_hash
            and isinstance(approved_at, str) and approved_at):
        _log.warning("approval record missing required fields — skipping")
        return None
    return ApprovalRecord(document=document, doc_hash=doc_hash,
                          approved_at=approved_at)


async def load_approvals(s3: S3StoreLike) -> list[ApprovalRecord]:
    """The approval history in chronological order. An empty list when there is none -- every
    project from before this feature is in that state, and then the audit log fallback
    decides."""
    keys = await s3.list(APPROVALS_PREFIX)
    records: list[ApprovalRecord] = []
    # The key starts with a timestamp, so lexicographic order == chronological order (a
    # property of ISO 8601; the same discipline ProjectRegistry.list_ids applies to
    # created_at).
    for key in sorted(keys):
        try:
            raw = await s3.get(key)
        except FileNotFoundError:
            continue  # deleted between the list and the get
        record = _parse(raw)
        if record is not None:
            records.append(record)
    return records
