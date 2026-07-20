# backend/pathfinder/routes/projects.py
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pathfinder import app as app_module
from pathfinder.project_store import write_manifest, delete_project_data

_log = logging.getLogger(__name__)

router = APIRouter()

class CreateProject(BaseModel):
    project_id: str
    name: str | None = None

@router.post("/projects")
async def create_project(body: CreateProject):
    if app_module.registry.is_registered(body.project_id):
        raise HTTPException(status_code=409, detail="project exists")
    sandbox = await app_module.make_sandbox(body.project_id)
    if app_module.durable_projects_enabled():
        try:
            await write_manifest(app_module.projects_root_s3_factory(),
                                 body.project_id, body.name)
        except Exception:
            # 스펙 결정: 재시작하면 사라질 프로젝트를 조용히 만들지 않는다.
            _log.exception("manifest write failed for %s", body.project_id)
            try:
                await sandbox.stop()
            except Exception:
                _log.exception("sandbox cleanup after manifest failure failed")
            raise HTTPException(status_code=500, detail="project persistence failed")
    app_module.registry.register(body.project_id, body.name)
    app_module.registry.attach(body.project_id, sandbox)
    return {"project_id": body.project_id, "name": body.name}

@router.get("/projects")
async def list_projects():
    # Minimal, in-memory listing only — no created-at/ownership/rich metadata.
    # Durable project metadata (DynamoDB) is a later MicroVM/prod concern, not
    # this backend-completion plan.
    return {
        "projects": [
            {"project_id": pid, "name": app_module.registry.get_name(pid)}
            for pid in app_module.registry.list_ids()
        ]
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
            await already_stopped.sandbox.stop()
        except Exception:
            _log.exception("sandbox stop failed for %s during delete (continuing)", pid)
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
            await removed.sandbox.stop()
        except Exception:
            _log.exception("sandbox stop failed for %s during final registry removal (continuing)", pid)
    return {"deleted": True}
