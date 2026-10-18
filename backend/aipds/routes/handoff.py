# backend/aipds/routes/handoff.py — API for the handoff tab.
#
# Assessment, supplementation, and generation all live in aipds/handoff/; this module only
# reads from the workspace and passes things along.
from __future__ import annotations

import asyncio
import io
import zipfile

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

import aipds.app as app_module
from aipds.handoff import package, readiness, supplement
from aipds.routes.deps import ensure_workspace

router = APIRouter()

_DISCOVERY = "aiplc-docs/discovery/"
_UPLOADS = "uploads/"


async def _paths(pid: str) -> list[str]:
    ws = await ensure_workspace(pid)
    return await ws.runner.list_files("aiplc-docs/**/*")


async def _readiness(pid: str, paths: list[str] | None = None) -> readiness.Readiness:
    ws = await ensure_workspace(pid)
    paths = paths if paths is not None else await _paths(pid)
    wanted = readiness.contents_needed(paths)
    texts = await asyncio.gather(*(ws.runner.read_file(p) for p in wanted))
    return readiness.assess(paths, dict(zip(wanted, texts)))


@router.get("/projects/{pid}/handoff/readiness", response_model=readiness.Readiness)
async def get_handoff_readiness(pid: str):
    """Which materials for building the handoff package exist and which are missing — the
    readiness screen of the handoff tab."""
    return await _readiness(pid)


async def _view(pid: str, s3, state: readiness.Readiness,
                record: supplement.Supplement) -> supplement.SupplementView:
    """Supplement screen. If there is a last package, its open decisions that are the PM's
    share are included along with their options — open decisions are not stored; they are
    read from the documents each time."""
    manifest = await package.load_manifest(s3)
    doc, kind = None, "vision"
    if manifest is not None and manifest.status == "ready":
        doc, kind = await package.stored_decisions(s3)
    if doc is None:
        return supplement.view(state, record)
    language = app_module.project_language(pid)
    return supplement.view(state, record, package.open_questions(doc, language, kind=kind),
                           await package.load_options(s3), has_package=True)


@router.get("/projects/{pid}/handoff/supplement", response_model=supplement.SupplementView)
async def get_handoff_supplement(pid: str):
    """Supplementary questions (empty sections, open decisions that are the PM's share) and
    their answers, plus the confirmation state of accepted AI suggestions."""
    state = await _readiness(pid)
    s3 = app_module.s3_store_factory(pid)
    return await _view(pid, s3, state, await supplement.load(s3))


@router.put("/projects/{pid}/handoff/supplement", response_model=supplement.SupplementView)
async def put_handoff_supplement(pid: str, body: supplement.SupplementUpdate):
    """Saves the whole form. Only answers and confirmations are recorded; AI-PLC artifacts
    are not touched."""
    state = await _readiness(pid)
    s3 = app_module.s3_store_factory(pid)
    try:
        record = supplement.apply(await supplement.load(s3), body)
    except supplement.InvalidSupplement as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await supplement.save(s3, record)
    return await _view(pid, s3, state, record)


class GenerateRequest(BaseModel):
    #: Keeps the finished stages of a previous failed or interrupted attempt and does only
    #: the rest. If the sources changed in the meantime, the server starts over from scratch
    #: (handoff/package.run).
    resume: bool = False


@router.post("/projects/{pid}/handoff/package", status_code=202,
             response_model=package.Manifest)
async def generate_handoff_package(pid: str, body: GenerateRequest | None = None):
    """Starts generation and returns immediately. The screen watches progress via GET
    (handoff/package header)."""
    paths = await _paths(pid)
    state = await _readiness(pid, paths)
    if state.blockers:
        raise HTTPException(status_code=409, detail={"blockers": state.blockers})
    if package.jobs.running(pid):
        raise HTTPException(status_code=409, detail="already generating")
    s3 = app_module.s3_store_factory(pid)
    ws = await ensure_workspace(pid)
    started = await package.start(s3, state, resume=bool(body and body.resume))
    package.jobs.spawn(pid, package.run(
        s3, read=ws.runner.read_file, paths=paths, readiness=state, language=app_module.project_language(pid),
        call=app_module.handoff_writer_factory(pid), started=started,
        folder=package.archive_folder(pid)))
    return started


@router.get("/projects/{pid}/handoff/package", response_model=package.PackageView)
async def get_handoff_package(pid: str):
    """The last package built: status, file contents, check results, and sources changed
    since it was built."""
    paths = await _paths(pid)
    ws = await ensure_workspace(pid)
    view = await package.view(app_module.s3_store_factory(pid), read=ws.runner.read_file,
                              paths=paths, running=package.jobs.running(pid))
    _fresh_start_prompt(pid, view.files)
    return view


def _fresh_start_prompt(pid: str, files: dict[str, str]) -> None:
    """Rewrites the start prompt with the current folder name. What is stored is the name at
    build time, so this keeps even packages built before the naming rule changed consistent
    with the paths in the downloaded zip — it is determined from the language and folder
    alone, with no model."""
    if package.START_PROMPT in files:
        files[package.START_PROMPT] = package.start_prompt(
            app_module.project_language(pid), package.archive_folder(pid))


class ResetResult(BaseModel):
    deleted: int


@router.delete("/projects/{pid}/handoff", response_model=ResetResult)
async def reset_handoff(pid: str):
    """Fully resets the handoff tab: package, previous versions, supplementary answers, and
    AI suggestion confirmations. Discovery artifacts are not touched. Rejected while
    generation is running — the running job would rewrite the manifest after the
    deletion."""
    await ensure_workspace(pid)
    if package.jobs.running(pid):
        raise HTTPException(status_code=409, detail="generating")
    return ResetResult(deleted=await package.reset(app_module.s3_store_factory(pid)))


def _content_disposition(pid: str) -> str:
    """The zip name equals the name of its top-level folder (package.archive_folder). Both
    are names converted to ASCII, so there is no need to supply a separate UTF-8 name."""
    return f'attachment; filename="{package.archive_folder(pid)}.zip"'


@router.get("/projects/{pid}/handoff/package/archive")
async def download_handoff_package(pid: str):
    """Under `{pid}-handoff/`: package files + Discovery sources (`discovery/`, evidence
    only) + uploaded source materials (`uploads/`). Prototype sources are not included."""
    s3 = app_module.s3_store_factory(pid)
    manifest = await package.load_manifest(s3)
    if manifest is None or manifest.status != "ready":
        raise HTTPException(status_code=404, detail="no package")
    paths = await _paths(pid)
    ws = await ensure_workspace(pid)
    built = await package.read_package(s3, manifest)
    _fresh_start_prompt(pid, built)
    raw = await package.read_sources(ws.runner.read_file, package.archive_originals(paths))
    uploads = await s3.list(_UPLOADS)
    buf = io.BytesIO()
    # Every entry goes under a single folder with the same name as the zip — the paths in
    # the start prompt point to it.
    root = package.archive_folder(pid) + "/"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in built.items():
            zf.writestr(root + name, text)
        for path, text in raw.items():
            zf.writestr(root + "discovery/" + path[len(_DISCOVERY):], text)
        # Uploads are kept byte-for-byte — the original shape of forms and regulations is
        # the input that development receives.
        for key in uploads:
            zf.writestr(root + key, await s3.get_bytes(key))
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": _content_disposition(pid)})
