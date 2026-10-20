# backend/aipds/routes/turns.py
import logging
from typing import AsyncIterator, Callable
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from aipds.parsers.redaction import redact_credentials
import aipds.app as app_module
from aipds.routes.deps import ensure_workspace
from aipds.audit_log import UserInput
from aipds.models import AgentEvent
from aipds.turn_job import TurnBusy, TurnJob, subscribe
from aipds.turn_marker import load_marker
from aipds.workspace import Workspace

router = APIRouter()
_log = logging.getLogger(__name__)

class MessageBody(BaseModel):
    text: str


class AnswersBody(BaseModel):
    answers: dict[str, str]


def _redacted(event: AgentEvent) -> AgentEvent:
    """Return a copy of event with credential-bearing content redacted.

    text AND payload are agent-authored content; kind/path stay structural.
    """
    updates = {}
    if event.text is not None:
        updates["text"] = redact_credentials(event.text)
    if event.payload is not None:
        updates["payload"] = redact_credentials(event.payload)
    return event.model_copy(update=updates) if updates else event


def start_turn(ws: Workspace, kind: str,
               events: Callable[[], AsyncIterator[AgentEvent]],
               input_text: str | None = None) -> TurnJob:
    """Start a turn as a server job. 409 if a turn is running -- the body carries that turn's id.

    Why the 409 carries the id: what the rejected side should do is not "send again"
    but **watch the turn that is already running**. This project has only one turn,
    so that is the turn the user was waiting for.

    Other routes (answer files, approval) also open turns through this -- so the shape
    of the 409 does not differ by path.

    `input_text` is what the person sent, as their bubble shows it (the audit record's text). `GET /turn` returns it, so a
    screen that attaches to a turn another screen started — the workspace after an approval or an answer change on the
    handoff tab — draws that bubble first instead of an AI reply to nothing.
    """
    try:
        return ws.turns.start(kind, events, input_text=input_text)
    except TurnBusy as busy:
        raise HTTPException(status_code=409, detail={
            "code": "turn_in_progress", "turn_id": busy.job.id})


def ensure_idle(ws: Workspace) -> None:
    """If a turn is running, raise the same 409 as `start_turn` **before any side effects**.

    Used by paths that write a record before the turn, such as writing an answer file
    or an approval record -- if the record stayed but the turn was rejected, the user
    would see an answer that looks applied but never reached the agent.
    """
    running = ws.turns.current()
    if running is not None:
        raise HTTPException(status_code=409, detail={
            "code": "turn_in_progress", "turn_id": running.id})


def turn_response(job: TurnJob, after: int = 0) -> EventSourceResponse:
    """Stream the turn log over SSE from after `after`. Each frame's `id` is the seq.

    The turn keeps running if the subscriber disconnects -- this generator only reads.
    To reattach, pass the last received seq as `after`.
    """
    async def gen():
        async for seq, event in subscribe(job, after):
            yield {"id": str(seq),
                   "data": _redacted(event).model_dump_json()}
    return EventSourceResponse(gen())


def _job_or_404(ws: Workspace, turn: str) -> TurnJob:
    job = ws.turns.get(turn)
    if job is None:
        # It finished long ago (retention period), is from before a restart, or is
        # another project's id. The screen then falls back to `GET /history`.
        raise HTTPException(status_code=404, detail="unknown turn")
    return job


@router.post("/projects/{pid}/turns")
async def create_turn(pid: str, body: MessageBody):
    """**Start** a turn and return its id. The stream is watched via `GET /events?turn=<id>`.

    Why the text comes in the body: EventSource supports only GET, and putting long
    input (especially Korean) in the URL grows the request line until the proxy returns
    431 (the observation is in createTurn in frontend lib/api/sse.ts). The workspace is
    checked here so a nonexistent project ends with 404.
    """
    ws = await ensure_workspace(pid)
    record = UserInput(text=body.text, source="chat",
                       language=app_module.project_language(pid))
    job = start_turn(ws, "message", lambda: ws.runner.send_message(body.text, record), record.text)
    return {"turn_id": job.id}


@router.get("/projects/{pid}/events")
async def stream_events(pid: str, turn: str, after: int = 0):
    """Watch one turn. `after` is the last seq already received (0 the first time).

    Turns are started by `POST /turns`, `POST /answers`, question-file answers, and
    approval -- this path only watches. Answer turns are watched through the same
    path: turns are distinguished by id, not by kind.
    """
    ws = await ensure_workspace(pid)
    return turn_response(_job_or_404(ws, turn), after)


@router.get("/projects/{pid}/turn")
async def get_turn(pid: str):
    """This project's current (or last) turn. The screen uses this on open to decide whether to attach.

    If there is no turn in memory but the S3 marker is `running`, that turn was cut
    off by a backend restart -- report it as `interrupted` (turn_marker.py header).
    """
    ws = await ensure_workspace(pid)
    job = ws.turns.latest()
    if job is not None:
        return {"turn": job.summary()}
    marker = await _load_marker_quietly(pid)
    if marker is not None and marker.get("state") == "running":
        return {"turn": {"turn_id": marker["turn_id"],
                         "kind": marker.get("kind", "message"),
                         "state": "interrupted", "last_seq": 0}}
    return {"turn": None}


async def _load_marker_quietly(pid: str) -> dict | None:
    if not app_module.durable_projects_enabled():
        return None     # Local development: there is no S3 to hold the marker
    try:
        return await load_marker(app_module.s3_store_factory(pid))
    except Exception:
        _log.exception("turn marker read failed")
        return None


@router.post("/projects/{pid}/answers")
async def create_answers_turn(pid: str, body: AnswersBody):
    """Start a turn from an answer submission. Takes a body for the same reason as `/turns`
    -- long free-text answers would hit the same URL length limit."""
    ws = await ensure_workspace(pid)
    # Question → answer as-is. Questions on this path come from the SDK question tool, and the key is the question text itself.
    text = "\n".join(f"{question} → {answer}" for question, answer in body.answers.items())
    record = UserInput(text=text, source="answers",
                       language=app_module.project_language(pid))
    job = start_turn(ws, "answers", lambda: ws.runner.send_answers(body.answers, record), record.text)
    return {"turn_id": job.id}


@router.get("/projects/{pid}/pending")
async def get_pending(pid: str):
    ws = await ensure_workspace(pid)
    payload = await ws.runner.pending()
    if payload is not None:
        payload = redact_credentials(payload)
    return {"pending": payload}

@router.post("/projects/{pid}/interrupt", status_code=202)
async def interrupt_turn(pid: str):
    """Interrupt the in-progress turn. Same contract as the prototype side
    (/prototypes/{slug}/interrupt).

    202 even with no turn in progress: interrupting is idempotent, and a user pressing
    again because nothing seems to happen is a normal path. It is 202 (Accepted)
    because the actual interrupt is a subprocess round trip and has not finished by
    the time of this response -- the SSE stream reports the outcome with a terminal
    event.
    """
    ws = await ensure_workspace(pid)   # 404 for a nonexistent project
    try:
        await ws.runner.interrupt()
    except Exception:
        # This means the user asked to interrupt but it did not actually take effect,
        # so it is logged -- but the 202/idempotent contract this docstring promises is
        # kept. The frontend just swallows failures (pressing again works), so there
        # is no reason to break with a 500.
        _log.exception("interrupt failed for %s", pid)
    return {"status": "interrupting"}
