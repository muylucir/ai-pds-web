# backend/aipds/routes/history.py
#
# The transcript lives in exactly one place, `projects/{pid}/discovery/transcript/
# ...` -- ClaudeDriver writes it to the store it received from
# `s3_store_factory(pid)` (agent/session_store.py's DiscoverySessionStore). The
# strands fallback driver used to write separately to `sessions/session_{pid}/...`,
# which is why this route once passed both stores; deleting that driver collapsed
# the prefixes into one.
import logging
from fastapi import APIRouter
from aipds import app as app_module
from aipds.routes.deps import ensure_workspace
from aipds.session_history import list_history

_log = logging.getLogger(__name__)
router = APIRouter()

@router.get("/projects/{pid}/history")
async def get_history(pid: str):
    await ensure_workspace(pid)  # 404 gate (unknown project) + lazy boot
    # A failure to build the store (missing credentials or bucket) empties the
    # history but does not block the screen -- this path's existing principle that
    # history is auxiliary data.
    try:
        project_s3 = app_module.s3_store_factory(pid)
    except Exception:
        _log.exception("project store unavailable for %s", pid)
        return {"items": []}
    return {"items": await list_history(project_s3, pid)}
