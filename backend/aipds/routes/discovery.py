from fastapi import APIRouter
from aipds.routes.deps import ensure_workspace

router = APIRouter()


@router.get("/projects/{pid}/questions")
async def list_questions(pid: str):
    paths = await (await ensure_workspace(pid)).list_question_files()
    return {"questions": paths}


@router.get("/projects/{pid}/artifacts")
async def list_artifacts(pid: str):
    paths = await (await ensure_workspace(pid)).list_artifacts()
    return {"artifacts": paths}
