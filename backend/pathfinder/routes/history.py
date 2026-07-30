# backend/pathfinder/routes/history.py
#
# 두 스토어를 함께 넘기는 이유는 드라이버 교체의 잔재다. 두 드라이버가 서로 다른
# S3 프리픽스에 트랜스크립트를 쓴다:
#
#   claude (현재 기본)  projects/{pid}/discovery/transcript/...
#                       ClaudeDriver가 s3_store_factory(pid)로 받은 스토어에 쓴다
#                       (agent/session_store.py의 DiscoverySessionStore).
#   strands (폴백)      sessions/session_{pid}/agents/agent_default/messages/...
#                       strands S3SessionManager가 쓴다.
#
# 한쪽만 넘기면 그 드라이버의 히스토리만 복원되고 다른 쪽은 조용히 빈 목록이
# 된다 — 이 버그가 정확히 그 모양이었다(읽는 프리픽스가 쓰는 곳과 달랐고,
# list_history의 강등이 그것을 삼켰다). 그래서 라우트가 두 스토어를 모두 만들어
# 넘기고, 어느 쪽에 내용이 있는지는 list_history가 판단한다.
import logging
from fastapi import APIRouter
from pathfinder import app as app_module
from pathfinder.routes.deps import ensure_workspace
from pathfinder.session_history import list_history

_log = logging.getLogger(__name__)
router = APIRouter()

@router.get("/projects/{pid}/history")
async def get_history(pid: str):
    await ensure_workspace(pid)  # 404 gate (unknown project) + lazy boot
    # 스토어 생성 실패(자격증명·버킷 미설정)는 히스토리를 비우되 화면은 막지
    # 않는다 — 히스토리는 보조 데이터라는 이 경로의 기존 원칙 그대로다. 두
    # 스토어를 따로 감싸는 이유: 한쪽 생성이 실패해도 다른 쪽으로는 복원될 수
    # 있어야 한다.
    try:
        project_s3 = app_module.s3_store_factory(pid)
    except Exception:
        _log.exception("project store unavailable for %s", pid)
        project_s3 = None
    try:
        session_s3 = app_module.session_s3_factory()
    except Exception:
        _log.exception("session store unavailable for %s", pid)
        session_s3 = None
    if project_s3 is None and session_s3 is None:
        return {"items": []}
    return {"items": await list_history(session_s3, pid,
                                        project_s3=project_s3)}
