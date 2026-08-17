# backend/pathfinder/routes/answers.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import pathfinder.app as app_module
from pathfinder.agent import prompts
from pathfinder.models import QuestionFile
from pathfinder.routes.deps import ensure_workspace

router = APIRouter()

class AnswersBody(BaseModel):
    answers: dict[str, str]


def _numbers(answers: dict[str, str]) -> dict[int, str]:
    try:
        return {int(k): v for k, v in answers.items()}
    except ValueError:
        raise HTTPException(status_code=400,
                            detail="question numbers must be integers")

@router.put("/projects/{pid}/questions/{name:path}", response_model=QuestionFile)
async def put_answers(pid: str, name: str, body: AnswersBody):
    try:
        return await (await ensure_workspace(pid)).put_answers(
            name, _numbers(body.answers))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="question file not found")
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/projects/{pid}/questions/{name:path}/answers")
async def submit_file_answers(pid: str, name: str, body: AnswersBody):
    """파일 질문 라운드의 답변 제출: 파일에 쓰고 **이어갈 턴의 핸들**을 돌려준다.

    왜 별 엔드포인트인가. `POST /answers`는 파킹된 `can_use_tool` future를 깨워
    **같은 턴**을 이어간다. 파일 질문 라운드에는 그 future가 없다 — PostToolUse
    훅이 `continue_: False`로 턴을 이미 끝냈다(claude_driver._on_post_tool_use).
    그래서 이어갈 곳이 턴이 아니라 파일이고, 에이전트는 **새 턴**으로 다시 불러야
    한다.

    새 턴의 텍스트를 백엔드가 만드는 이유: 에이전트가 읽는 문장은 프로젝트 언어를
    따라야 한다(agent/prompts.py 헤더). 프론트가 만들면 두 언어를 프론트가
    관리하게 되고, 그것이 2026-08-04 결함의 모양이다.

    스트림 엔드포인트를 새로 만들지 않는다 — 핸들을 기존 `GET /events?turn=`로
    열면 된다. 그 2단계가 존재하는 이유(URL 길이 → HTTP 431)가 자유 서술 답변에도
    그대로 적용된다(turn_handles.py 헤더).
    """
    ws = await ensure_workspace(pid)
    try:
        qfile = await ws.put_answers(name, _numbers(body.answers))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="question file not found")
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    text = prompts.file_answers_recorded(app_module.project_language(pid), name)
    return {"turn_id": app_module.turn_handles.create(pid, {"text": text}),
            "questions": qfile}
