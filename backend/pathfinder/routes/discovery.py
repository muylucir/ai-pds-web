from fastapi import APIRouter
from pathfinder.routes.deps import get_workspace

router = APIRouter()


@router.get("/projects/{pid}/questions")
async def list_questions(pid: str):
    paths = await get_workspace(pid).list_question_files()
    return {"questions": paths}


@router.get("/projects/{pid}/artifacts")
async def list_artifacts(pid: str):
    paths = await get_workspace(pid).list_artifacts()
    return {"artifacts": paths}
