# backend/aipds/routes/handoff.py — 인계 탭의 API.
#
# 판정은 전부 aipds/handoff/에 있고 여기는 워크스페이스에서 읽어 넘기기만 한다.
import asyncio

from fastapi import APIRouter, HTTPException

import aipds.app as app_module
from aipds.handoff import readiness, supplement
from aipds.routes.deps import ensure_workspace

router = APIRouter()


async def _readiness(pid: str) -> readiness.Readiness:
    ws = await ensure_workspace(pid)
    paths = await ws.runner.list_files("aiplc-docs/**/*")
    wanted = readiness.contents_needed(paths)
    texts = await asyncio.gather(*(ws.runner.read_file(p) for p in wanted))
    return readiness.assess(paths, dict(zip(wanted, texts)))


@router.get("/projects/{pid}/handoff/readiness", response_model=readiness.Readiness)
async def get_handoff_readiness(pid: str):
    """인계 패키지를 만들 재료가 무엇이 있고 무엇이 없는가 — 인계 탭의 준비 상태 화면."""
    return await _readiness(pid)


@router.get("/projects/{pid}/handoff/supplement", response_model=supplement.SupplementView)
async def get_handoff_supplement(pid: str):
    """재료가 없는 섹션의 보완 문항과 그 답, AI 제안 수락 항목의 확인 상태."""
    state = await _readiness(pid)
    record = await supplement.load(app_module.s3_store_factory(pid))
    return supplement.view(state, record)


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
    return supplement.view(state, record)
