# backend/pathfinder/routes/deps.py
import asyncio
import logging
from fastapi import HTTPException
from pathfinder import app as app_module
from pathfinder.workspace import Workspace

_log = logging.getLogger(__name__)
# pid별 부팅 lock — 복원 직후 동시 요청 2건이 VM을 두 번 띄우는 것을 막는다.
_boot_locks: dict[str, asyncio.Lock] = {}


async def ensure_workspace(pid: str) -> Workspace:
    """살아있는 워크스페이스를 반환하고, 복원-등록만 된 프로젝트면 이 자리에서
    워크스페이스를 lazy 초기화(로컬 디렉토리)한다(스펙: 복원 시점 = 첫 접근).
    미등록 404, 초기화 실패 503(등록은 유지 — 다음 요청이 재시도)."""
    try:
        return app_module.registry.get(pid)
    except KeyError:
        pass
    if not app_module.registry.is_registered(pid):
        raise HTTPException(status_code=404, detail="unknown project")
    lock = _boot_locks.setdefault(pid, asyncio.Lock())
    async with lock:
        try:
            return app_module.registry.get(pid)  # double-check: 앞선 요청이 이미 초기화
        except KeyError:
            pass
        try:
            workspace = await app_module.make_workspace(pid)
        except Exception:
            _log.exception("lazy workspace init failed for %s", pid)
            raise HTTPException(status_code=503, detail="project workspace unavailable")
        try:
            return app_module.registry.attach(pid, workspace)
        except KeyError:
            # 초기화 대기 중 DELETE /projects/{pid}가 끼어든 경우: 프로젝트는 이미
            # 미등록 상태다. 방금 만든 워크스페이스의 러너가 새지 않도록 best-effort로
            # 정지하고 평소 미등록-프로젝트 시맨틱과 동일하게 404를 낸다.
            try:
                await workspace.runner.stop()
            except Exception:
                _log.exception("failed to stop runner for deleted-during-boot project %s", pid)
            raise HTTPException(status_code=404, detail="unknown project")
