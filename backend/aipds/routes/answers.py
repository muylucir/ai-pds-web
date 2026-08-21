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
    """Answer submission for a file question round: write the file and return **a handle
    for the turn that continues from it**.

    Why a separate endpoint. `POST /answers` wakes a parked `can_use_tool` future and
    continues **the same turn**. A file question round has no such future -- the
    PostToolUse hook already ended the turn with `continue_: False`
    (claude_driver._on_post_tool_use). So what the answers return to is the file, not
    the turn, and the agent has to be called again in a **new turn**.

    Why the backend composes that new turn's text: sentences the agent reads have to
    follow the project language (agent/prompts.py's header). Having the frontend
    compose it would put both languages in the frontend's hands, which is the shape of
    the 2026-08-04 defect.

    No new stream endpoint is added -- the handle opens through the existing
    `GET /events?turn=`. The reason that two-step exists (URL length -> HTTP 431)
    applies to free-text answers just as much (turn_handles.py's header).
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
    # The answers ride in the turn text. That text stays in the transcript as the
    # user's bubble and history restore draws it verbatim, so without the answers
    # every round of a restored conversation reads as the same sentence (see the
    # rationale in prompts.file_answers_recorded).
    #
    # **Rendering is owned in one place by the backend (2026-08-21).** `qfile` holds
    # the option list, so expanding letters into labels can only happen here -- and
    # returning that result in the response removes any reason for the frontend to
    # implement the same discrimination a second time, making the screen and the
    # record **the same string** (the full story is in aipds/answer_summary.py's
    # header).
    #
    # Why `numbered` is passed: `body.answers` has the string keys the frontend sent,
    # which sort lexicographically ("12" < "2"). Question numbers have to be numeric
    # to line up with the question list.
    summary = answer_summary(qfile, numbered, language)
    text = prompts.file_answers_recorded(language, name, summary)
    # If the state file does not exist yet, point the resume turn at it.
    #
    # The hook and the turn-boundary reconciliation (agent/reconcile.py) move the
    # file onto the screen **when it exists**. With no file there is nothing to move,
    # and only the agent can create it -- because only the agent knows the stage
    # names (the rationale is in prompts.state_file_missing).
    #
    # Why the test is `current_stage is None` rather than "file missing": it is
    # broader. The shape the upstream rules require always has a Current Stage line,
    # so the absence of that line means there is **no readable state**, whether the
    # file is missing or corrupt.
    try:
        if (await ws.get_state()).current_stage is None:
            text += "\n\n" + prompts.state_file_missing(language)
    except Exception:
        # Failing to read the state must not block the answer submission: the user
        # has already submitted the form, and this note is only a secondary
        # instruction to bring the badges back.
        logging.getLogger("aipds.agent").exception(
            "state probe for the resume turn failed — continuing without the note")
    # `summary` is the string the frontend uses **verbatim** for the bubble.
    # `text` (the turn text the model reads) contains it and appends the instruction
    # -- the human-readable part first and the machine instruction as a tail is the
    # principle `approvalMarker.ts` records.
    return {"turn_id": app_module.turn_handles.create(pid, {"text": text}),
            "summary": summary,
            "questions": qfile}
