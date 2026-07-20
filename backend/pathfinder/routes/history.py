# backend/pathfinder/routes/history.py
from fastapi import APIRouter
from pathfinder import app as app_module
from pathfinder.routes.deps import get_workspace
from pathfinder.session_history import list_history

router = APIRouter()

@router.get("/projects/{pid}/history")
async def get_history(pid: str):
    get_workspace(pid)  # 404 gate (unknown project)
    s3 = app_module.session_s3_factory()
    return {"items": await list_history(s3, pid)}
