# backend/pathfinder/routes/projects.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pathfinder import app as app_module

router = APIRouter()

class CreateProject(BaseModel):
    project_id: str
    name: str | None = None

@router.post("/projects")
async def create_project(body: CreateProject):
    try:
        app_module.registry.get(body.project_id)
        raise HTTPException(status_code=409, detail="project exists")
    except KeyError:
        pass
    sandbox = await app_module.make_sandbox(body.project_id)
    app_module.registry.create(body.project_id, sandbox, name=body.name)
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
