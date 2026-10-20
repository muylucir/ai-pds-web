# backend/aipds/routes/answers.py
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import aipds.app as app_module
from aipds.agent import prompts
from aipds.answer_summary import answer_summary
from aipds.audit_log import UserInput
from aipds.routes.deps import ensure_workspace
from aipds.routes.turns import ensure_idle, start_turn

router = APIRouter()

class AnswersBody(BaseModel):
    answers: dict[str, str]


def _numbers(answers: dict[str, str]) -> dict[int, str]:
    try:
        return {int(k): v for k, v in answers.items()}
    except ValueError:
        raise HTTPException(status_code=400,
                            detail="question numbers must be integers")

@router.post("/projects/{pid}/questions/{name:path}/answers")
async def submit_file_answers(pid: str, name: str, body: AnswersBody):
    """Submit answers for a file question round: write them to the file, **start the turn that continues**, and return its id.

    Why a separate endpoint. `POST /answers` wakes the parked `can_use_tool` future and
    continues **the same turn**. A file question round has no such future — the PostToolUse
    hook already ended the turn with `continue_: False` (claude_driver._on_post_tool_use).
    So what continues is the file, not the turn, and the agent must be called again in a
    **new turn**.

    Why the backend builds the new turn's text: sentences the agent reads must follow the
    project language (agent/prompts.py header). If the frontend built them, the frontend
    would have to manage two languages, and that is the shape of the 2026-08-04 defect.

    No new stream endpoint is created — the returned id can be watched with the existing
    `GET /events?turn=`. If a turn is running, the request is rejected with 409 **before**
    writing to the file (`ensure_idle`).
    """
    ws = await ensure_workspace(pid)
    ensure_idle(ws)
    numbered = _numbers(body.answers)
    try:
        qfile = await ws.put_answers(name, numbered)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="question file not found")
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    language = app_module.project_language(pid)
    # Carry the answers in the turn text. This text stays in the transcript as the user's
    # bubble and history restoration draws it as is, so without the answers every round of
    # the restored conversation would look like the same phrase (see the rationale in
    # prompts.file_answers_recorded).
    #
    # **The backend owns rendering as a single copy (2026-08-21).** `qfile` carries the
    # option list, so resolving a letter to its label can only happen here, and returning
    # the result in the response removes any reason for the frontend to implement the same
    # resolution a second time — the screen and the record become **the same string** (the
    # full story is in the aipds/answer_summary.py header).
    #
    # Why pass `numbered`: `body.answers` has the string keys the frontend sent, which sort
    # lexicographically ("12" < "2"). Question numbers must be numeric to match the question list.
    summary = answer_summary(qfile, numbered, language)
    text = prompts.file_answers_recorded(language, name, summary)
    # If the state file does not exist yet, point the resume turn at it.
    #
    # The hook and turn-boundary reconciliation (agent/reconcile.py) bring the file to the
    # screen **when it exists**. If the file itself is missing, there is nothing to bring,
    # and only the agent can create it — because only the agent knows the stage name
    # (the rationale is in prompts.state_file_missing).
    #
    # Why decide by `current_stage is None`: it is broader than the file being absent. The
    # form the upstream rules require always has a Current Stage line, so the absence of that
    # line means **there is no readable state**, whether the file is missing or corrupted.
    try:
        if (await ws.get_state()).current_stage is None:
            text += "\n\n" + prompts.state_file_missing(language)
    except Exception:
        # Failing to read state does not block answer submission — the user has already
        # submitted the form, and this note is only an auxiliary instruction to keep the badge alive.
        logging.getLogger("aipds.agent").exception(
            "state probe for the resume turn failed — continuing without the note")
    # `summary` is the string the frontend uses **as is** in the bubble. `text` (the turn
    # text the model reads) contains it and appends instructions — the human-readable part
    # comes first and the machine instructions form the tail, which is the principle recorded
    # in `approvalMarker.ts`.
    # Only what the user chose goes into the audit log — the tail of `text` is instructions for the agent.
    record = UserInput(text=summary, source="answers", language=language,
                       detail=name.rsplit("/", 1)[-1])
    job = start_turn(ws, "message", lambda: ws.runner.send_message(text, record), record.text)
    return {"turn_id": job.id,
            "summary": summary,
            "questions": qfile}
