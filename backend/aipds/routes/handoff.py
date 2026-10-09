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


async def _cited(pid: str, s3, state: readiness.Readiness) -> dict[str, list[str]] | None:
    """마지막으로 만든 패키지의 PRD가 인용한 AI 제안. 만든 패키지가 없으면 None."""
    manifest = await package.load_manifest(s3)
    if manifest is None or manifest.status != "ready":
        return None
    try:
        prd = await s3.get(package.PACKAGE_PREFIX + "PRD.md")
    except FileNotFoundError:
        return None
    return package.cited_suggestions(prd, state, app_module.project_language(pid))


@router.get("/projects/{pid}/handoff/supplement", response_model=supplement.SupplementView)
async def get_handoff_supplement(pid: str):
    """재료가 없는 섹션의 보완 문항과 그 답, AI 제안 수락 항목의 확인 상태."""
    state = await _readiness(pid)
    s3 = app_module.s3_store_factory(pid)
    return supplement.view(state, await supplement.load(s3), await _cited(pid, s3, state))


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
    return supplement.view(state, record, await _cited(pid, s3, state))


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


def _content_disposition(pid: str) -> str:
    """artifacts의 같은 이름 함수와 같은 이유 — pid는 비-ASCII일 수 있다."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", pid).strip("-") or "project"
    utf8 = quote(f"{pid}-handoff.zip", safe="")
    return f'attachment; filename="{safe}-handoff.zip"; filename*=UTF-8\'\'{utf8}'


@router.get("/projects/{pid}/handoff/package/archive")
async def download_handoff_package(pid: str):
    """패키지 네 파일 + Discovery 원본(있는 것만, `discovery/` 아래). 프로토타입 소스는 넣지 않는다."""
    s3 = app_module.s3_store_factory(pid)
    manifest = await package.load_manifest(s3)
    if manifest is None or manifest.status != "ready":
        raise HTTPException(status_code=404, detail="no package")
    paths = await _paths(pid)
    ws = await ensure_workspace(pid)
    originals = [p for p in paths if p.startswith(_DISCOVERY)]
    built = await package.read_package(s3, manifest)
    raw = await package.read_sources(ws.runner.read_file, originals)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in built.items():
            zf.writestr(name, text)
        for path, text in raw.items():
            zf.writestr("discovery/" + path[len(_DISCOVERY):], text)
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": _content_disposition(pid)})
