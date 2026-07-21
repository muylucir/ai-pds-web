# backend/pathfinder/routes/artifacts.py
from fastapi import APIRouter, HTTPException
from pathfinder.routes.deps import ensure_workspace
from pathfinder.parsers.redaction import redact_credentials

router = APIRouter()

@router.get("/projects/{pid}/state")
async def get_state(pid: str):
    return await (await ensure_workspace(pid)).get_state()

@router.get("/projects/{pid}/audit")
async def get_audit(pid: str):
    return await (await ensure_workspace(pid)).get_audit()

@router.get("/projects/{pid}/document")
async def get_document(pid: str):
    return {"markdown": await (await ensure_workspace(pid)).get_document()}

@router.get("/projects/{pid}/questions/{name:path}")
async def get_questions(pid: str, name: str):
    try:
        return await (await ensure_workspace(pid)).get_questions(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="question file not found")

@router.get("/projects/{pid}/files/{path:path}")
async def read_artifact(pid: str, path: str):
    # Review-screen-only general-purpose file viewer — outputs (aiplc-docs/)
    # only. uploads/ and other input paths are not artifacts, so they are not
    # exposed here (403).
    if not path.startswith("aiplc-docs/"):
        raise HTTPException(status_code=403, detail="artifacts only")
    try:
        ws = await ensure_workspace(pid)
        content = await ws.runner.read_file(path)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="not found")
    return {"content": redact_credentials(content)}
