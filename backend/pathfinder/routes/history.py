# backend/pathfinder/routes/history.py
import logging
from fastapi import APIRouter
from pathfinder import app as app_module
from pathfinder.routes.deps import ensure_workspace
from pathfinder.session_history import list_history

_log = logging.getLogger(__name__)
router = APIRouter()

@router.get("/projects/{pid}/history")
async def get_history(pid: str):
    await ensure_workspace(pid)  # 404 gate (unknown project) + lazy boot
    try:
        s3 = app_module.session_s3_factory()
    except Exception:
        _log.exception("session store unavailable for %s", pid)
        return {"items": []}
    return {"items": await list_history(s3, pid)}
