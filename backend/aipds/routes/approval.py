# backend/aipds/routes/approval.py -- the document approval gate.
#
# Why a separate route (why sending "approve" through POST /message is not enough):
# on that path the only record of an approval was **the audit.md the agent writes**.
# When the decision depends on the agent's prose, a change of phrasing makes the
# decision disappear -- measured, 3 of 5 approval gates went unrecognised
# (approval_store.py's header).
#
# This route **writes a structured record of the button press first**, then runs the
# agent turn. The order is the contract: the approval survives even if the turn
# fails.
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

import aipds.app as app_module
from aipds.approval_store import load_approvals, save_approval
from aipds.routes.deps import ensure_workspace

router = APIRouter()
_log = logging.getLogger(__name__)

#: What can be approved. The gate attaches only to discovery-document.md (the
#: review screen's isDiscoveryDocument) -- no other file has a notion of approval.
_DOC_PATH = "aiplc-docs/discovery/discovery-document.md"


def _hash(text: str) -> str:
    """A fingerprint of the document at the moment of approval.

    This turns the invalidation test from a guess into a fact. It used to infer "the
    document changed" by searching the audit log's prose for words meaning
    "modified"/"update", and ordinary progress narration contains those words often
    enough that approvals were invalidated arbitrarily (measured: pilot1's idx=40
    "Written to Living Document" erased the approval at idx=37).
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@router.post("/projects/{pid}/approve")
async def approve_document(pid: str):
    """Approve the document. Write the record first, then run the agent turn."""
    ws = await ensure_workspace(pid)
    try:
        text = await ws.runner.read_file(_DOC_PATH)
    except (FileNotFoundError, ValueError):
        # Writing a record with an empty hash makes the invalidation test
        # permanently meaningless (it never equals anything it is compared with). The
        # gate only appears while looking at the document, so this cannot arrive
        # through the normal path -- but passing it through quietly would leave
        # nobody aware of that.
        raise HTTPException(status_code=409,
                            detail="there is no discovery document to approve")

    s3 = app_module.s3_store_factory(pid)
    await save_approval(s3, document=_DOC_PATH, doc_hash=_hash(text),
                        approved_at=datetime.now(timezone.utc).isoformat())

    # The turn runs **after** the record. The agent still needs to move to the next
    # stage and leave a human-readable entry in audit.md (the upstream rules require
    # it), but whether that succeeds must not decide whether the approval exists.
    #
    # The approval text follows the project language: this turn stays in the
    # transcript as the user's speech and the agent is conversing in that language
    # (the frontend's approvalMarker.ts records the same judgement).
    language = app_module.project_language(pid)
    turn_text = "Approved" if language == "en" else "승인"
    try:
        async for _ in ws.runner.send_message(turn_text):
            pass
    except Exception:
        # The approval is already recorded. A failed turn is something the user can
        # retry, and returning a 500 here would make them believe the approval did
        # not happen.
        _log.exception("approval turn failed after the record was saved")

    return {"approved": True}


@router.get("/projects/{pid}/approvals")
async def list_approvals(pid: str):
    """The approval history plus **the current document hash**.

    Why the current hash is returned here: if the frontend computed it, the hash
    algorithm would exist in two places, and the moment they diverge an approval goes
    permanently unrecognised. That failure is silent -- the gate simply does not open,
    which makes the cause hard to find. The definition of the hash is owned by
    whoever writes the approvals.

    An empty list when there is no history: every project from before this feature is
    in that state, and the frontend then decides via the audit-log fallback.
    """
    ws = await ensure_workspace(pid)
    records = await load_approvals(app_module.s3_store_factory(pid))
    try:
        current = _hash(await ws.runner.read_file(_DOC_PATH))
    except (FileNotFoundError, ValueError):
        # With no document there is nothing to compare against. This has to be null
        # rather than an empty string: a frontend that reads it as "there is a hash"
        # would decide the approval state wrongly.
        current = None
    return {
        "approvals": [
            {"document": r.document, "doc_hash": r.doc_hash,
             "approved_at": r.approved_at}
            for r in records
        ],
        "current_doc_hash": current,
    }
