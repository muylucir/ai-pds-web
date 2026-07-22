# backend/pathfinder/routes/projects.py
import asyncio
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from pathfinder import app as app_module
from pathfinder.parsers.state import parse_state_file
from pathfinder.project_store import write_manifest, delete_project_data

_log = logging.getLogger(__name__)

router = APIRouter()

_STATE_PATH = "aiplc-docs/aiplc-state.md"


async def _progress(pid: str) -> dict | None:
    """페이지 내 프로젝트의 진행상황. S3 직접 읽기 — ensure_workspace를 타지
    않는다(목록 조회가 N개 워크스페이스 lazy 초기화를 유발하면 안 됨).
    fail-soft: 어떤 실패도 None으로 강등, 목록 응답은 막지 않는다."""
    if not app_module.durable_projects_enabled():
        return None
    try:
        md = await app_module.s3_store_factory(pid).get(_STATE_PATH)
        state = parse_state_file(md)
    except Exception:
        return None
    if not state.stages:          # 파일은 있지만 스테이지 파싱 결과가 비면 표시할 게 없다
        return None
    return {
        "current_stage": state.current_stage,
        "completed": sum(1 for s in state.stages if s.status == "completed"),
        "total": len(state.stages),
    }

class CreateProject(BaseModel):
    project_id: str
    name: str | None = None

@router.post("/projects")
async def create_project(body: CreateProject):
    if app_module.registry.is_registered(body.project_id):
        raise HTTPException(status_code=409, detail="project exists")
    workspace = await app_module.make_workspace(body.project_id)
    # 매니페스트와 레지스트리가 같은 created_at을 갖도록 여기서 확정 —
    # 목록 정렬(생성일 오름차순) 기준이 재시작 전후로 달라지지 않는다.
    created_at = datetime.now(timezone.utc).isoformat()
    if app_module.durable_projects_enabled():
        try:
            await write_manifest(app_module.projects_root_s3_factory(),
                                 body.project_id, body.name, created_at=created_at)
        except Exception:
            # 스펙 결정: 재시작하면 사라질 프로젝트를 조용히 만들지 않는다.
            _log.exception("manifest write failed for %s", body.project_id)
            try:
                await workspace.runner.stop()
            except Exception:
                _log.exception("workspace cleanup after manifest failure failed")
            raise HTTPException(status_code=500, detail="project persistence failed")
    app_module.registry.register(body.project_id, body.name, created_at=created_at)
    app_module.registry.attach(body.project_id, workspace)
    return {"project_id": body.project_id, "name": body.name}

@router.get("/projects")
async def list_projects(page: int = Query(1, ge=1), size: int = Query(10, ge=1, le=50)):
    # 페이지 단위 목록 + S3 진행상황(fail-soft). 워크스페이스 lazy 초기화는
    # 유발하지 않는다 — durable 프로젝트 메타데이터(DynamoDB)는 이후 prod 관심사.
    ids = app_module.registry.list_ids()
    total = len(ids)
    page_ids = ids[(page - 1) * size : page * size]
    progresses = await asyncio.gather(*(_progress(pid) for pid in page_ids))
    return {
        "projects": [
            {"project_id": pid, "name": app_module.registry.get_name(pid),
             "created_at": app_module.registry.get_created_at(pid), "progress": prog}
            for pid, prog in zip(page_ids, progresses)
        ],
        "total": total,
        "page": page,
        "size": size,
    }

@router.delete("/projects/{pid}")
async def delete_project(pid: str):
    """전부 삭제(스펙 결정): VM stop(베스트에포트) → S3 세션+산출물 삭제
    (실패 시 500, 멱등 재시도) → 레지스트리 제거."""
    if not app_module.registry.is_registered(pid):
        raise HTTPException(status_code=404, detail="unknown project")
    already_stopped = None
    if app_module.registry.has_workspace(pid):
        already_stopped = app_module.registry.get(pid)
        try:
            await already_stopped.runner.stop()
        except Exception:
            _log.exception("runner stop failed for %s during delete (continuing)", pid)
    if app_module.durable_projects_enabled():
        try:
            await delete_project_data(app_module.session_s3_factory(),
                                      app_module.projects_root_s3_factory(), pid)
        except Exception:
            _log.exception("S3 delete failed for %s", pid)
            raise HTTPException(status_code=500, detail="project delete failed")
    removed = app_module.registry.remove(pid)
    # 역방향 레이스 대비: has_workspace가 False라 위 stop 블록을 건너뛰었더라도,
    # S3 삭제 await 도중 동시 ensure_workspace가 부팅을 마치고 attach했을 수
    # 있다. 마지막 remove가 반환한 워크스페이스가 그 정리 지점이 된다 — 이미
    # 위에서 stop한 것과 동일 객체면(레이스가 없었던 정상 경로) 중복 stop을
    # 피하고, 다른 객체면(늦게 attach된 워크스페이스) stop해 VM이 새지 않게 한다.
    if removed is not None and removed is not already_stopped:
        try:
            await removed.runner.stop()
        except Exception:
            _log.exception("runner stop failed for %s during final registry removal (continuing)", pid)
    return {"deleted": True}
