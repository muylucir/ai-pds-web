# backend/aipds/routes/handoff.py — 인계 탭의 API.
#
# 판정은 전부 aipds/handoff/에 있고 여기는 워크스페이스에서 읽어 넘기기만 한다.
import asyncio

from fastapi import APIRouter

from aipds.handoff import readiness
from aipds.routes.deps import ensure_workspace

router = APIRouter()


@router.get("/projects/{pid}/handoff/readiness", response_model=readiness.Readiness)
async def get_handoff_readiness(pid: str):
    """인계 패키지를 만들 재료가 무엇이 있고 무엇이 없는가 — 인계 탭의 준비 상태 화면."""
    ws = await ensure_workspace(pid)
    paths = await ws.runner.list_files("aiplc-docs/**/*")
    wanted = readiness.contents_needed(paths)
    texts = await asyncio.gather(*(ws.runner.read_file(p) for p in wanted))
    return readiness.assess(paths, dict(zip(wanted, texts)))
