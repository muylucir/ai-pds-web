# backend/aipds/routes/uploads.py
from fastapi import APIRouter, HTTPException, Request, UploadFile
from aipds.routes.deps import ensure_workspace
from aipds.parsers.uploads import convert, upload_key, MAX_UPLOAD_BYTES

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
    # No list-then-name step: the key carries a fresh uuid, so there is no
    # window for two concurrent uploads to agree on one key.
    path = upload_key(file.filename or "upload")
    if not await ws.runner.write_file_if_absent(path, content):
        # Impossible in practice (fresh uuid per upload) -- surfaced as a
        # retryable conflict rather than a silent overwrite.
        raise HTTPException(status_code=409, detail="upload key already exists")
    return {"path": path, "chars": len(content), "truncated": truncated}
