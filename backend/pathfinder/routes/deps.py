# backend/pathfinder/routes/deps.py
from fastapi import HTTPException
from pathfinder import app as app_module
from pathfinder.workspace import Workspace

def get_workspace(pid: str) -> Workspace:
    try:
        return app_module.registry.get(pid)
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown project")
