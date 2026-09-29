# backend/aipds/routes/history.py
#
# 트랜스크립트는 `projects/{pid}/discovery/transcript/...` 한 곳에만 있다 —
# ClaudeDriver가 `s3_store_factory(pid)`로 받은 스토어에 쓴다
# (agent/session_store.py의 DiscoverySessionStore). 예전에는 strands 폴백
# 드라이버가 `sessions/session_{pid}/...`에 따로 써서 라우트가 두 스토어를 모두
# 넘겼는데, 그 드라이버를 삭제하면서 프리픽스도 하나가 됐다.
import logging
from fastapi import APIRouter
from aipds import app as app_module
from aipds.routes.deps import ensure_workspace
from aipds.context_usage import DISCOVERY_KEY, load_record
from aipds.session_history import list_history

_log = logging.getLogger(__name__)
router = APIRouter()

@router.get("/projects/{pid}/history")
async def get_history(pid: str):
    await ensure_workspace(pid)  # 404 gate (unknown project) + lazy boot
    # 스토어 생성 실패(자격증명·버킷 미설정)는 히스토리를 비우되 화면은 막지
    # 않는다 — 히스토리는 보조 데이터라는 이 경로의 기존 원칙 그대로다.
    try:
        project_s3 = app_module.s3_store_factory(pid)
    except Exception:
        _log.exception("project store unavailable for %s", pid)
        return {"items": []}
    return {"items": await list_history(project_s3, pid)}


@router.get("/projects/{pid}/context")
async def get_context(pid: str):
    """마지막 턴의 컨텍스트 사용량(aipds/context_usage.py). 없으면 null.

    히스토리와 같은 자리에 있는 이유: 둘 다 "화면이 열릴 때 되살리는 보조 데이터"이고
    같은 강등 규칙을 따른다 — 읽지 못하면 표시가 없을 뿐 화면은 막지 않는다. 도는 턴은
    `context` 이벤트로 이 값을 덮는다.
    """
    await ensure_workspace(pid)
    try:
        project_s3 = app_module.s3_store_factory(pid)
    except Exception:
        _log.exception("project store unavailable for %s", pid)
        return {"context": None}
    return {"context": await load_record(project_s3, DISCOVERY_KEY)}
