# backend/aipds/routes/approval.py — document approval gate.
#
# Why a separate route (why sending "승인" (approve) as a chat turn is not enough): on
# that path, the only record of approval was **the audit.md the agent writes**. When
# the decision depends on the agent's prose, it disappears whenever the wording
# drifts; as observed, 3 of 5 approval gates were not recognized (approval_store.py
# header).
#
# This route **first records the user's click as a structured record**, and only then
# starts the agent turn. The order is the contract: even if the turn fails, the
# approval remains.
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

import aipds.app as app_module
from aipds.approval_store import load_approvals, save_approval
from aipds.audit_log import UserInput
from aipds.routes.deps import ensure_workspace
from aipds.routes.turns import ensure_idle, start_turn

router = APIRouter()
_log = logging.getLogger(__name__)

#: The approval target. The gate is attached only to discovery-document.md (the review
#: screen's isDiscoveryDocument); other files have no notion of approval.
_DOC_PATH = "aiplc-docs/discovery/discovery-document.md"


def _hash(text: str) -> str:
    """Fingerprint of the document at approval time.

    This turns the invalidation decision from a guess into a fact. Previously, "the
    document changed" was inferred by searching the audit log's prose for
    `수정|update|갱신`, but normal progress narration often contains those words, so
    approvals were invalidated arbitrarily (observed: ws-m idx=40's "Written to
    Living Document" erased the approval at idx=37).
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@router.post("/projects/{pid}/approve")
async def approve_document(pid: str):
    """Approve the document. Write the record first, then **start** the agent turn.

    The turn is not awaited within this request: the turn that moves on to the next
    stage takes minutes, and if the request stayed open that long the proxy timeout
    (CloudFront 60 s) would turn the approval response into a 504. The turn runs as
    server work (turn_job.py), and the screen watches it via the returned `turn_id` or
    `GET /turn`.

    If a turn is running, it is a 409 **before** the record. An approval received
    while the agent is editing the document soon becomes invalid because the hash
    diverges, and if only the record were kept, the user would see "승인됨" (approved)
    while the agent is never told about it.
    """
    ws = await ensure_workspace(pid)
    ensure_idle(ws)
    try:
        text = await ws.runner.read_file(_DOC_PATH)
    except (FileNotFoundError, ValueError):
        # Writing a record with an empty hash would make the invalidation decision
        # permanently meaningless (it would not equal anything it is compared with).
        # The gate only appears while the document is shown, so the normal path never
        # gets here, but letting it pass silently means nobody would know.
        raise HTTPException(status_code=409,
                            detail="there is no discovery document to approve")

    s3 = app_module.s3_store_factory(pid)
    await save_approval(s3, document=_DOC_PATH, doc_hash=_hash(text),
                        approved_at=datetime.now(timezone.utc).isoformat())

    # The turn runs **after** the record. The agent still needs to proceed to the next
    # stage and leave a human-readable record in audit.md (required by the upstream
    # rules), but whether that succeeds must not determine whether the approval
    # exists.
    #
    # The approval text follows the project language: this turn is kept in the
    # transcript as a user utterance, and the agent is conversing in that language
    # (the frontend's approvalMarker.ts records the same decision).
    language = app_module.project_language(pid)
    turn_text = "Approved" if language == "en" else "승인"
    record = UserInput(text=turn_text, source="approval", language=language)
    job = start_turn(ws, "message", lambda: ws.runner.send_message(turn_text, record), record.text)
    return {"approved": True, "turn_id": job.id}


@router.get("/projects/{pid}/approvals")
async def list_approvals(pid: str):
    """Approval history + the **current document hash**.

    Why the current hash is returned here too: if the frontend computed it itself, the hash
    algorithm would exist in two places, and the moment the two diverged, approvals would
    never be recognized again. That failure is silent: the gate simply does not open, so the
    cause is hard to find. The side that writes approvals owns the hash definition.

    With no history it is an empty list; every project from before this feature is in that
    state, and then the frontend decides via the audit-log fallback.
    """
    ws = await ensure_workspace(pid)
    # History and document have no reason to wait on each other; read them together.
    records, doc = await asyncio.gather(
        load_approvals(app_module.s3_store_factory(pid)),
        ws.runner.read_file(_DOC_PATH),
        return_exceptions=True)
    if isinstance(records, BaseException):
        raise records
    try:
        if isinstance(doc, BaseException):
            raise doc
        current = _hash(doc)
    except (FileNotFoundError, ValueError):
        # Without a document there is nothing to compare. It must be null, not an
        # empty string; if the frontend mistook it for "there is a hash", it would
        # misjudge the approval status.
        current = None
    return {
        "approvals": [
            {"document": r.document, "doc_hash": r.doc_hash,
             "approved_at": r.approved_at}
            for r in records
        ],
        "current_doc_hash": current,
    }
