# backend/aipds/routes/handoff.py — 인계 탭의 API.
#
# 판정·보완·생성은 전부 aipds/handoff/에 있고 여기는 워크스페이스에서 읽어 넘기기만 한다.
from __future__ import annotations

import asyncio
import io
import re
import zipfile
from urllib.parse import quote

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
    """인계 패키지를 만들 재료가 무엇이 있고 무엇이 없는가 — 인계 탭의 준비 상태 화면."""
    return await _readiness(pid)


async def _view(pid: str, s3, state: readiness.Readiness,
                record: supplement.Supplement) -> supplement.SupplementView:
    """보완 화면. 마지막 패키지가 있으면 그 열린 결정 중 PM 몫과 그 보기를 함께 싣는다 — 열린
    결정은 저장하지 않고 문서에서 그때마다 읽는다."""
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
    """보완 문항(빈 섹션 · 열린 결정 중 PM 몫)과 그 답, AI 제안 수락 항목의 확인 상태."""
    state = await _readiness(pid)
    s3 = app_module.s3_store_factory(pid)
    return await _view(pid, s3, state, await supplement.load(s3))


@router.put("/projects/{pid}/handoff/supplement", response_model=supplement.SupplementView)
async def put_handoff_supplement(pid: str, body: supplement.SupplementUpdate):
    """폼 전체를 저장한다. 답과 확인만 기록하고 AI-PLC 산출물은 건드리지 않는다."""
    state = await _readiness(pid)
    s3 = app_module.s3_store_factory(pid)
    try:
        record = supplement.apply(await supplement.load(s3), body)
    except supplement.InvalidSupplement as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await supplement.save(s3, record)
    return await _view(pid, s3, state, record)


class GenerateRequest(BaseModel):
    #: 실패·중단된 지난 시도의 끝난 단계를 두고 나머지만 한다. 원본이 그사이 바뀌었으면
    #: 서버가 처음부터 한다(handoff/package.run).
    resume: bool = False


@router.post("/projects/{pid}/handoff/package", status_code=202,
             response_model=package.Manifest)
async def generate_handoff_package(pid: str, body: GenerateRequest | None = None):
    """생성을 시작하고 곧바로 돌아온다. 화면은 GET으로 진행을 본다(handoff/package 헤더)."""
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
        call=app_module.handoff_writer_factory(pid), started=started))
    return started


@router.get("/projects/{pid}/handoff/package", response_model=package.PackageView)
async def get_handoff_package(pid: str):
    """마지막으로 만든 패키지: 상태, 파일 내용, 검사 결과, 만든 뒤 바뀐 원본."""
    paths = await _paths(pid)
    ws = await ensure_workspace(pid)
    return await package.view(app_module.s3_store_factory(pid), read=ws.runner.read_file,
                              paths=paths, running=package.jobs.running(pid))


class ResetResult(BaseModel):
    deleted: int


@router.delete("/projects/{pid}/handoff", response_model=ResetResult)
async def reset_handoff(pid: str):
    """인계 탭을 완전히 초기화한다: 패키지, 이전 판, 보완 답과 AI 제안 확인. Discovery 산출물은
    건드리지 않는다. 생성 중에는 거절한다 — 돌던 작업이 지운 뒤에 manifest를 다시 쓴다."""
    await ensure_workspace(pid)
    if package.jobs.running(pid):
        raise HTTPException(status_code=409, detail="generating")
    return ResetResult(deleted=await package.reset(app_module.s3_store_factory(pid)))


def _content_disposition(pid: str) -> str:
    """artifacts의 같은 이름 함수와 같은 이유 — pid는 비-ASCII일 수 있다."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", pid).strip("-") or "project"
    utf8 = quote(f"{pid}-handoff.zip", safe="")
    return f'attachment; filename="{safe}-handoff.zip"; filename*=UTF-8\'\'{utf8}'


@router.get("/projects/{pid}/handoff/package/archive")
async def download_handoff_package(pid: str):
    """`aipds-handoff/` 아래에 패키지 파일 + Discovery 원본(`discovery/`, 근거만) + 업로드 원자료
    (`uploads/`). 프로토타입 소스는 넣지 않는다."""
    s3 = app_module.s3_store_factory(pid)
    manifest = await package.load_manifest(s3)
    if manifest is None or manifest.status != "ready":
        raise HTTPException(status_code=404, detail="no package")
    paths = await _paths(pid)
    ws = await ensure_workspace(pid)
    built = await package.read_package(s3, manifest)
    raw = await package.read_sources(ws.runner.read_file, package.archive_originals(paths))
    uploads = await s3.list(_UPLOADS)
    buf = io.BytesIO()
    # 모든 항목을 최상위 폴더 하나 아래 둔다 — 시작 프롬프트의 경로가 그것을 가리킨다
    # (package.ARCHIVE_ROOT).
    root = package.ARCHIVE_ROOT + "/"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in built.items():
            zf.writestr(root + name, text)
        for path, text in raw.items():
            zf.writestr(root + "discovery/" + path[len(_DISCOVERY):], text)
        # 업로드는 바이트 그대로다 — 양식·규정의 원래 모양이 개발이 받을 입력이다.
        for key in uploads:
            zf.writestr(root + key, await s3.get_bytes(key))
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": _content_disposition(pid)})
