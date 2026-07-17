# backend/pathfinder/routes/artifacts.py
from fastapi import APIRouter, HTTPException
from pathfinder.routes.deps import get_workspace

router = APIRouter()

@router.get("/projects/{pid}/state")
async def get_state(pid: str):
    return await get_workspace(pid).get_state()

@router.get("/projects/{pid}/audit")
async def get_audit(pid: str):
    return await get_workspace(pid).get_audit()

@router.get("/projects/{pid}/document")
async def get_document(pid: str):
    return {"markdown": await get_workspace(pid).get_document()}

@router.get("/projects/{pid}/questions/{name:path}")
async def get_questions(pid: str, name: str):
    try:
        return await get_workspace(pid).get_questions(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="question file not found")
