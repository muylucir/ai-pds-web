# backend/aipds/approval_store.py — a structured record of approval decisions.
#
# **Why this file exists.** Approval status was being reconstructed with regexes from the prose of
# `audit.md`. The fact that the user pressed the gate button is known with certainty at that
# moment, yet the structure threw it away and tried to recover it from what the agent transcribed
# in natural language.
#
# Measured (41 entries in ws-m's audit.md): only 2 of 5 approval gates were recognized. Because
# users answer in chat — "승인" (approve) was recognized, but "동의" (agree; the final approval!),
# "진행" (proceed), and a multiple-choice "A" all failed. On top of that, normal progress narration
# contained 'update', so the invalidation check wiped even the approvals that remained. All that
# was left on screen was "기록된 승인 이력이 없습니다".
#
# The same symptom was fixed three times (ca8c508 parser, 68e143f display condition, e18d681 language).
# Every fix was about "how do we read the agent's output", which is a value we do not control. So
# we change the basis of judgment to **values we write**. This record is the primary basis, and
# audit log parsing is demoted to a fallback for existing projects that have no records.
#
# **It does not replace audit.md.** The human-readable audit trail is still written by the agent
# according to the upstream rules (rule/ is data — we do not modify it). This record is a copy for
# machine judgment, and since the two have different roles, both remain.
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass

from aipds.s3store import S3StoreLike

_log = logging.getLogger("aipds.approval")

#: S3Store prepends projects/{pid}/, so this is a project-relative path (same discipline as
#: pending_store). Listing by this prefix returns the project's entire approval history.
APPROVALS_PREFIX = "approvals/"


@dataclass(frozen=True)
class ApprovalRecord:
    """A single approval decision.

    doc_hash is the core of the invalidation check. Previously we searched the audit log prose for
    `수정|update|갱신` to guess "the document changed, so re-approval is needed", but normal
    progress narration often contains those words, causing false positives (measured: idx=40).
    Keeping the document hash at approval time lets us compare against the current document and
    judge it **as fact**.
    """
    document: str
    doc_hash: str
    approved_at: str


def _key(approved_at: str) -> str:
    """Timestamp + random suffix. Without the suffix, two approvals in the same second (rapid
    re-approval clicks) would have the later overwrite the earlier and lose the audit record. Colons are
    awkward to handle in S3 keys, so they are replaced with '-'."""
    stamp = approved_at.replace(":", "-")
    return f"{APPROVALS_PREFIX}{stamp}-{uuid.uuid4().hex[:8]}.json"


async def save_approval(s3: S3StoreLike, *, document: str, doc_hash: str,
                        approved_at: str) -> None:
    """Record an approval. Accumulates instead of overwriting — approvals are history."""
    await s3.put(_key(approved_at), json.dumps({
        "document": document,
        "doc_hash": doc_hash,
        "approved_at": approved_at,
    }, ensure_ascii=False))


def _parse(raw: str) -> ApprovalRecord | None:
    """One corrupted entry must not lose the entire history (the same judgment as pending_store
    returning None for a corrupted payload)."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        _log.warning("approval record is not valid JSON — skipping")
        return None
    if not isinstance(data, dict):
        return None
    document, doc_hash = data.get("document"), data.get("doc_hash")
    approved_at = data.get("approved_at")
    # Missing fields would blow up later in the hash comparison — filter them out at the read site.
    # Each value is narrowed separately instead of with `all(...)` because the type checker cannot
    # propagate isinstance inside a generator outward.
    if not (isinstance(document, str) and document
            and isinstance(doc_hash, str) and doc_hash
            and isinstance(approved_at, str) and approved_at):
        _log.warning("approval record missing required fields — skipping")
        return None
    return ApprovalRecord(document=document, doc_hash=doc_hash,
                          approved_at=approved_at)


async def load_approvals(s3: S3StoreLike) -> list[ApprovalRecord]:
    """Approval history in chronological order. An empty list if none — every project from
    before this feature is in that state, and then the audit log fallback decides."""
    # Keys start with a timestamp, so lexicographic order == chronological order (a property of ISO 8601,
    # the same discipline ProjectRegistry.list_ids uses for created_at).
    keys = sorted(await s3.list(APPROVALS_PREFIX))
    # Parallel GETs — `gather` returns results in input order, so the sort above is the result order
    # (same pattern as session_store.load_transcript).
    bodies = await asyncio.gather(*(s3.get(k) for k in keys),
                                 return_exceptions=True)
    records: list[ApprovalRecord] = []
    for body in bodies:
        if isinstance(body, FileNotFoundError):
            continue  # deleted between list and get
        if isinstance(body, BaseException):
            raise body
        record = _parse(body)
        if record is not None:
            records.append(record)
    return records
