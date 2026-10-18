# backend/aipds/routes/uploads.py
import asyncio

from fastapi import APIRouter, HTTPException, Request, UploadFile
from aipds.routes.deps import ensure_workspace
from aipds.parsers.uploads import prepare, MAX_UPLOAD_BYTES

router = APIRouter()
_TOO_BIG = f"file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit"

@router.post("/projects/{pid}/uploads")
async def upload_file(pid: str, file: UploadFile, request: Request):
    ws = await ensure_workspace(pid)
    # Cheap pre-check: reject oversized uploads before reading the body.
    # Content-Length is client-controlled (not a security boundary — the
    # post-read check below remains authoritative) but stops honest large
    # uploads from spooling to disk first.
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_UPLOAD_BYTES + 10_000:  # multipart overhead margin
        raise HTTPException(status_code=413, detail=_TOO_BIG)
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=_TOO_BIG)
    # Document parsing (up to 10MB) uses CPU — running it on the loop would stall every SSE stream
    # in the meantime.
    try:
        upload = await asyncio.to_thread(prepare, file.filename or "", data)
    except ValueError as e:
        raise HTTPException(status_code=415, detail=str(e))
    # No list-then-name step: the keys carry a fresh uuid, so there is no
    # window for two concurrent uploads to agree on one key. A False write is
    # impossible in practice for the same reason -- surfaced as a retryable
    # conflict rather than a silent overwrite.
    if upload.original_key is not None and not await ws.runner.write_bytes_if_absent(
            upload.original_key, upload.original):
        raise HTTPException(status_code=409, detail="upload key already exists")
    if upload.text_key is not None and not await ws.runner.write_file_if_absent(
            upload.text_key, upload.text):
        raise HTTPException(status_code=409, detail="upload key already exists")
    # `path` is the file the agent should read first, and `original` is the original kept
    # alongside the converted copy (PDF — read when charts or scanned pages are needed). If
    # there is only the original, it is the `path`.
    return {
        "path": upload.text_key or upload.original_key,
        "original": upload.original_key if upload.text_key else None,
        "chars": len(upload.text or ""),
        "truncated": upload.truncated,
    }
