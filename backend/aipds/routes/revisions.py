# backend/aipds/routes/revisions.py — what a document looked like before the user's answers.
#
# The rounds are taken when answers are handed over (aipds/doc_revisions.py). These two reads are all
# the screen needs: which baselines exist for a document, and the text of one. The comparison itself
# is drawn in the browser (frontend lib/docDiff.ts) — it is a rendering concern, and the current text
# is already there.
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from aipds import doc_revisions
from aipds.agent.reconcile import is_document
from aipds.parsers.redaction import redact_credentials
from aipds.routes.deps import ensure_workspace

router = APIRouter()


def _check_path(path: str) -> None:
    # Only documents have rounds; anything else (uploads/, the audit log) is refused rather than
    # answered with an empty list, so a wrong path is visible as wrong.
    if not is_document(path):
        raise HTTPException(status_code=400, detail="not a document")


@router.get("/projects/{pid}/revisions")
async def list_baselines(pid: str, path: str):
    """Baselines for `path`, newest first — only those that differ from the current text
    (`doc_revisions.baselines_for`). Empty for a document no round has seen change, and for every
    project from before rounds existed: there is nothing to compare, and the screen shows the plain document."""
    _check_path(path)
    ws = await ensure_workspace(pid)
    try:
        current = await ws.runner.read_file(path)
    except (FileNotFoundError, ValueError):
        return {"baselines": []}
    rounds = await doc_revisions.load_rounds(ws.runner, pid)
    picked = doc_revisions.baselines_for(rounds, path, doc_revisions.content_hash(current))
    return {"baselines": [
        {"id": r.id, "at": r.at, "kind": r.kind, "source": r.source,
         "existed": path in r.documents}
        for r in picked
    ]}


@router.get("/projects/{pid}/revisions/{round_id}")
async def read_baseline(pid: str, round_id: str, path: str):
    """The text of `path` in that round, redacted the same way as `GET /files` — the two are compared
    line by line, so they must go through the same transform. `content` is null if the document did
    not exist yet."""
    _check_path(path)
    if not doc_revisions.is_round_id(round_id):
        raise HTTPException(status_code=400, detail="bad round id")
    ws = await ensure_workspace(pid)
    try:
        text = await doc_revisions.baseline_text(ws.runner, pid, round_id, path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="not found")
    return {"content": None if text is None else redact_credentials(text)}
