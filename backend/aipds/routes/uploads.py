# backend/aipds/routes/uploads.py
import asyncio

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
    # PDF·엑셀 파싱(최대 5MB)은 CPU를 쓴다 — 루프 위에서 돌리면 그동안 모든
    # SSE 스트림이 멈춘다.
    try:
        content, truncated = await asyncio.to_thread(
            convert, file.filename or "", data)
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
