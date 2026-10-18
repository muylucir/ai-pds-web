# backend/aipds/routes/history.py
#
# The transcript lives in only one place, `projects/{pid}/discovery/transcript/...` --
# ClaudeDriver writes to the store it gets from `s3_store_factory(pid)`
# (DiscoverySessionStore in agent/session_store.py). The strands fallback driver used
# to write separately to `sessions/session_{pid}/...`, so the route passed both
# stores; when that driver was deleted, the prefix became one as well.
import logging
from fastapi import APIRouter
from aipds import app as app_module
from aipds.routes.deps import ensure_workspace
from aipds.context_usage import DISCOVERY_KEY, load_record
from aipds.session_history import list_history

_log = logging.getLogger(__name__)
router = APIRouter()

@router.get("/projects/{pid}/history")
async def get_history(pid: str):
    await ensure_workspace(pid)  # 404 gate (unknown project) + lazy boot
    # A store creation failure (credentials or bucket not configured) empties the
    # history but does not block the screen -- the same existing principle of this
    # path that history is auxiliary data.
    try:
        project_s3 = app_module.s3_store_factory(pid)
    except Exception:
        _log.exception("project store unavailable for %s", pid)
        return {"items": []}
    return {"items": await list_history(project_s3, pid)}


@router.get("/projects/{pid}/context")
async def get_context(pid: str):
    """The last turn's context usage (aipds/context_usage.py). null if none.

    Why it sits alongside history: both are "auxiliary data restored when the screen
    opens" and follow the same degradation rule -- if it cannot be read, nothing is
    shown, but the screen is not blocked. A running turn overwrites this value with a
    `context` event.
    """
    await ensure_workspace(pid)
    try:
        project_s3 = app_module.s3_store_factory(pid)
    except Exception:
        _log.exception("project store unavailable for %s", pid)
        return {"context": None}
    return {"context": await load_record(project_s3, DISCOVERY_KEY)}
