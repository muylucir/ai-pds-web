# backend/pathfinder/routes/uploads.py
from fastapi import APIRouter, HTTPException, UploadFile
from pathfinder.routes.deps import get_workspace
from pathfinder.parsers.uploads import convert, safe_name, MAX_UPLOAD_BYTES

router = APIRouter()

@router.post("/projects/{pid}/uploads")
async def upload_file(pid: str, file: UploadFile):
    ws = get_workspace(pid)
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file exceeds 5MB limit")
    try:
        content, truncated = convert(file.filename or "", data)
    except ValueError as e:
        raise HTTPException(status_code=415, detail=str(e))
    existing = set(
        p.removeprefix("uploads/") for p in await ws.sandbox.list_files("uploads/*"))
    name = safe_name(file.filename or "upload", existing)
    path = f"uploads/{name}"
    await ws.sandbox.write_file(path, content)
    return {"path": path, "chars": len(content), "truncated": truncated}
