# backend/pathfinder/routes/projects.py
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pathfinder import app as app_module
from pathfinder.project_store import write_manifest

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
