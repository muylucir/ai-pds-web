# backend/aipds/routes/artifacts.py
import asyncio
import io
import re
import zipfile
from urllib.parse import quote
from fastapi import APIRouter, HTTPException, Response
from aipds.routes.deps import ensure_workspace
from aipds.parsers.doc_references import missing_references
from aipds.parsers.redaction import redact_credentials

router = APIRouter()


def _content_disposition(pid: str) -> str:
    """RFC 6266/5987 format — pid can be unvalidated user input (non-ASCII, including Korean
    project names), so raw interpolation raises UnicodeEncodeError (500) under latin-1 header
    encoding. An ASCII-safe filename fallback and the filename*=UTF-8'' extended form are carried
    together to secure both browser compatibility and safety."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", pid).strip("-") or "artifacts"
    utf8 = quote(f"{pid}-artifacts.zip", safe="")
    return f'attachment; filename="{safe}-artifacts.zip"; filename*=UTF-8\'\'{utf8}'

@router.get("/projects/{pid}/state")
async def get_state(pid: str):
    return await (await ensure_workspace(pid)).get_state()

@router.get("/projects/{pid}/audit")
async def get_audit(pid: str):
    return await (await ensure_workspace(pid)).get_audit()

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

#: The entry document handed to the development organization. AI-DLC Inception reads this one
#: page.
_HANDOFF_DOC = "aiplc-docs/discovery/discovery-document.md"


@router.get("/projects/{pid}/artifacts/missing-references")
async def get_missing_references(pid: str):
    """Files the Discovery Document points to as evidence but that are not in the artifacts —
    document review shows them as a warning next to '전체 다운로드' (download all)
    (parsers/doc_references header). If there is no document yet, the list is empty: there is
    nothing to point to."""
    ws = await ensure_workspace(pid)
    paths = await ws.runner.list_files("aiplc-docs/**/*")
    if _HANDOFF_DOC not in paths:
        return {"document": None, "missing": []}
    text = await ws.runner.read_file(_HANDOFF_DOC)
    return {"document": _HANDOFF_DOC, "missing": missing_references(text, paths)}


@router.get("/projects/{pid}/artifacts/archive")
async def download_artifacts_archive(pid: str):
    """All of aiplc-docs/** as a zip — document review's '전체 다운로드' (download all). 404 if
    there are no artifacts. The content is the S3 original (the audit is already
    redacted-at-rest)."""
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
        headers={"Content-Disposition": _content_disposition(pid)},
    )
