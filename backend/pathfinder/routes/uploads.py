# backend/pathfinder/routes/uploads.py
from fastapi import APIRouter, HTTPException, Request, UploadFile
from pathfinder.routes.deps import ensure_workspace
from pathfinder.parsers.uploads import convert, safe_name, MAX_UPLOAD_BYTES

router = APIRouter()

@router.post("/projects/{pid}/uploads")
async def upload_file(pid: str, file: UploadFile, request: Request):
    ws = await ensure_workspace(pid)
    # Cheap pre-check: reject oversized uploads before reading the body.
    # Content-Length is client-controlled (not a security boundary — the
    # post-read check below remains authoritative) but stops honest large
    # uploads from spooling to disk first.
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_UPLOAD_BYTES + 10_000:  # multipart overhead margin
        raise HTTPException(status_code=413, detail="file exceeds 5MB limit")
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
