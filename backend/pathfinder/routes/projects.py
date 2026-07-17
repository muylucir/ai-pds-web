# backend/pathfinder/routes/projects.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pathfinder import app as app_module

router = APIRouter()

class CreateProject(BaseModel):
    project_id: str

@router.post("/projects")
async def create_project(body: CreateProject):
    try:
        app_module.registry.get(body.project_id)
        raise HTTPException(status_code=409, detail="project exists")
    except KeyError:
        pass
    sandbox = await app_module.make_sandbox(body.project_id)
    app_module.registry.create(body.project_id, sandbox)
    return {"project_id": body.project_id}
