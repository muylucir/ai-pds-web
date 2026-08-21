# backend/aipds/routes/answers.py
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import aipds.app as app_module
from aipds.agent import prompts
from aipds.answer_summary import answer_summary
from aipds.models import QuestionFile
from aipds.routes.deps import ensure_workspace

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
    numbered = _numbers(body.answers)
    try:
        qfile = await ws.put_answers(name, numbered)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="question file not found")
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    language = app_module.project_language(pid)
    # 답변을 턴 텍스트에 함께 싣는다. 이 텍스트가 트랜스크립트에 사용자 말풍선으로
    # 남고 히스토리 복원이 그것을 그대로 그리므로, 답변이 없으면 복원된 대화의 모든
    # 라운드가 같은 문구로 보인다(prompts.file_answers_recorded의 근거 참조).
    #
    # **렌더는 백엔드가 한 벌로 소유한다(2026-08-21).** `qfile`이 보기 목록을 들고
    # 있으므로 letter를 라벨로 푸는 것은 여기서만 할 수 있고, 그 결과를 응답으로 함께
    # 돌려주면 프론트가 같은 판별을 두 번째로 구현할 이유가 없어진다 — 화면과 기록이
    # **같은 문자열**이 된다(aipds/answer_summary.py 헤더에 그 전말이 있다).
    #
    # `numbered`를 넘기는 이유: `body.answers`는 프론트가 보낸 문자열 키이고
    # 정렬이 사전순이 된다("12" < "2"). 문항 번호는 숫자여야 문항 목록과 맞는다.
    summary = answer_summary(qfile, numbered, language)
    text = prompts.file_answers_recorded(language, name, summary)
    # 상태 파일이 아직 없으면 재개 턴에 그것을 지목한다.
    #
    # 훅과 턴 경계 재조정(agent/reconcile.py)은 파일이 **있을 때** 그것을 화면으로
    # 옮긴다. 파일 자체가 없으면 옮길 것이 없고, 그것을 만들 수 있는 것은
    # 에이전트뿐이다 — 스테이지 이름을 아는 것이 에이전트뿐이기 때문이다
    # (prompts.state_file_missing에 그 근거가 있다).
    #
    # `current_stage is None`으로 판정하는 이유: 파일 부재보다 넓다. 상류 룰이
    # 요구하는 형태에는 항상 Current Stage 줄이 있으므로, 그 줄이 없다는 것은
    # 파일이 없든 손상됐든 **읽을 상태가 없다**는 뜻이다.
    try:
        if (await ws.get_state()).current_stage is None:
            text += "\n\n" + prompts.state_file_missing(language)
    except Exception:
        # 상태를 읽지 못하는 것으로 답변 제출을 막지 않는다 — 사용자는 폼을 이미
        # 제출했고, 이 노트는 배지를 살리는 보조 지시일 뿐이다.
        logging.getLogger("aipds.agent").exception(
            "state probe for the resume turn failed — continuing without the note")
    # `summary`는 프론트가 말풍선에 **그대로** 쓰는 문자열이다. `text`(모델이 읽는
    # 턴 텍스트)는 그것을 포함하고 뒤에 지시를 붙인다 — 사람이 읽을 부분이 앞에 오고
    # 기계용 지시가 꼬리가 되는 것이 `approvalMarker.ts`가 적어 둔 원칙이다.
    return {"turn_id": app_module.turn_handles.create(pid, {"text": text}),
            "summary": summary,
            "questions": qfile}
