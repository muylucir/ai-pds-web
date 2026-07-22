# backend/pathfinder/routes/artifacts.py
import asyncio
import io
import zipfile
from fastapi import APIRouter, HTTPException, Response
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

@router.get("/projects/{pid}/artifacts/archive")
async def download_artifacts_archive(pid: str):
    """aiplc-docs/** 전체를 zip으로 — 문서 리뷰의 '전체 다운로드'. 산출물이
    없으면 404. 콘텐츠는 S3 원문(오디트는 이미 redacted-at-rest)."""
    ws = await ensure_workspace(pid)
    paths = await ws.runner.list_files("aiplc-docs/**/*")
    if not paths:
        raise HTTPException(status_code=404, detail="no artifacts")
    contents = await asyncio.gather(*(ws.runner.read_file(p) for p in paths))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, content in zip(paths, contents):
            zf.writestr(path, content)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{pid}-artifacts.zip"'},
    )
