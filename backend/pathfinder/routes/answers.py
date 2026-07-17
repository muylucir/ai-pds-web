# backend/pathfinder/routes/answers.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pathfinder.routes.deps import get_workspace

router = APIRouter()

class AnswersBody(BaseModel):
    answers: dict[str, str]

@router.put("/projects/{pid}/questions/{name:path}")
async def put_answers(pid: str, name: str, body: AnswersBody):
    answers = {int(k): v for k, v in body.answers.items()}
    try:
        return await get_workspace(pid).put_answers(name, answers)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="question file not found")
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
