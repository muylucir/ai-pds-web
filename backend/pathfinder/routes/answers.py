# backend/pathfinder/routes/answers.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pathfinder.models import QuestionFile
from pathfinder.routes.deps import ensure_workspace

router = APIRouter()

class AnswersBody(BaseModel):
    answers: dict[str, str]

@router.put("/projects/{pid}/questions/{name:path}", response_model=QuestionFile)
async def put_answers(pid: str, name: str, body: AnswersBody):
    try:
        answers = {int(k): v for k, v in body.answers.items()}
    except ValueError:
        raise HTTPException(status_code=400, detail="question numbers must be integers")
    try:
        return await (await ensure_workspace(pid)).put_answers(name, answers)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="question file not found")
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
