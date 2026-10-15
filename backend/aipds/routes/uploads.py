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
    # 문서 파싱(최대 10MB)은 CPU를 쓴다 — 루프 위에서 돌리면 그동안 모든
    # SSE 스트림이 멈춘다.
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
    # `path`는 에이전트가 먼저 읽을 파일, `original`은 변환본과 함께 둔 원본
    # (PDF — 도표·스캔 페이지가 필요할 때 읽는다). 원본만 있으면 그것이 `path`다.
    return {
        "path": upload.text_key or upload.original_key,
        "original": upload.original_key if upload.text_key else None,
        "chars": len(upload.text or ""),
        "truncated": upload.truncated,
    }
