# backend/aipds/routes/turns.py
import json
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from aipds.parsers.redaction import redact_credentials
import aipds.app as app_module
from aipds.routes.deps import ensure_workspace
from aipds.models import AgentEvent, TurnResult

router = APIRouter()
_log = logging.getLogger(__name__)

class MessageBody(BaseModel):
    text: str


class AnswersBody(BaseModel):
    answers: dict[str, str]


def _turn_payload(pid: str, handle: str | None, inline: object,
                  key: str) -> object:
    """Take the turn's input from a handle or from inline query parameters.

    The handle path is the default: long input in the URL grows the request line
    until a proxy returns 431 (measured in aipds/turn_handles.py's header). The
    inline path is kept because deployment is not atomic -- the moment the backend
    goes up first, an older frontend is still sending ?text= / ?answers=.

    Neither present is a 400. Quietly running an empty turn would leave the user
    looking at a bubble with no response and no way to tell why.
    """
    if handle is not None:
        payload = app_module.turn_handles.consume(pid, handle)
        if payload is None:
            # Expired, reused, or belonging to another project -- which one is
            # not disclosed, so that a handle's existence carries no information.
            raise HTTPException(status_code=400,
                                detail="turn handle is unknown or already used")
        return payload[key]
    if inline is None:
        raise HTTPException(status_code=400,
                            detail=f"either `turn` or `{key}` is required")
    return inline

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

@router.post("/projects/{pid}/message")
async def post_message(pid: str, body: MessageBody):
    ws = await ensure_workspace(pid)
    events = [_redacted(e) async for e in ws.runner.send_message(body.text)]
    return TurnResult(events=events)

@router.post("/projects/{pid}/turns")
async def create_turn(pid: str, body: MessageBody):
    """Take the turn text in the **body** and return a short handle.

    EventSource supports GET only and cannot carry a body, so this two-step is the
    only way to keep long input out of the URL (see aipds/turn_handles.py's header).
    The workspace is checked here so an unknown project ends as a 404: if the client
    got a handle and then hit a 404 on the stream, all the user would see is
    "the connection dropped".
    """
    await ensure_workspace(pid)
    return {"turn_id": app_module.turn_handles.create(pid, {"text": body.text})}


@router.get("/projects/{pid}/events")
async def stream_events(pid: str, turn: str | None = None,
                        text: str | None = None):
    ws = await ensure_workspace(pid)
    resolved = _turn_payload(pid, turn, text, "text")
    async def gen():
        async for event in ws.runner.send_message(resolved):
            yield {"data": _redacted(event).model_dump_json()}
    return EventSourceResponse(gen())

@router.get("/projects/{pid}/events/live")
async def stream_live(pid: str):
    """Reattach to a turn in progress. **No handle.**

    The other stream paths require a single-use 60-second handle created by a `POST`
    -- that exists to keep long input out of the URL (turn_handles.py), which makes
    it unusable for reattaching. This path has no input, so there is nothing to
    carry: it just watches a turn that is already running.

    With no turn to attach to it ends with a single `done` (not an error). The
    frontend then restores the screen from `GET /history` -- a user returning late to
    a turn that has since finished is the normal path.
    """
    ws = await ensure_workspace(pid)
    async def gen():
        async for event in ws.runner.reattach():
            yield {"data": _redacted(event).model_dump_json()}
    return EventSourceResponse(gen())


@router.post("/projects/{pid}/answers")
async def create_answers_turn(pid: str, body: AnswersBody):
    """Issue a handle for an answer submission. Same reason as `/turns`: a long
    free-text answer runs into the same URL length limit."""
    await ensure_workspace(pid)
    return {"turn_id": app_module.turn_handles.create(pid,
                                                      {"answers": body.answers})}


@router.get("/projects/{pid}/answers/stream")
async def stream_answers(pid: str, turn: str | None = None,
                         answers: str | None = None):
    ws = await ensure_workspace(pid)
    raw = _turn_payload(pid, turn, answers, "answers")
    # The handle path already holds a dict (the POST body validated it). Only the
    # inline path needs parsing.
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400,
                                detail="answers must be a JSON object")
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="answers must be a JSON object")
    async def gen():
        async for event in ws.runner.send_answers(raw):
            yield {"data": _redacted(event).model_dump_json()}
    return EventSourceResponse(gen())

@router.get("/projects/{pid}/pending")
async def get_pending(pid: str):
    ws = await ensure_workspace(pid)
    payload = await ws.runner.pending()
    if payload is not None:
        payload = redact_credentials(payload)
    return {"pending": payload}

@router.post("/projects/{pid}/interrupt", status_code=202)
async def interrupt_turn(pid: str):
    """Interrupt the turn in progress. The same contract as the prototype side
    (/prototypes/{slug}/interrupt).

    202 even with no turn running: interruption is idempotent, and a user pressing
    again because nothing seemed to happen is the normal path. It is 202 (Accepted)
    because the actual interruption is a subprocess round trip and is not finished by
    the time this response goes out -- the outcome is reported by the SSE stream's
    terminal event.
    """
    ws = await ensure_workspace(pid)   # an unknown project is a 404
    try:
        await ws.runner.interrupt()
    except Exception:
        # This means the user asked to interrupt and it did not actually take, so
        # it is logged -- but the 202/idempotent contract this docstring promises is
        # still honoured. The frontend simply swallows the failure (the user can
        # press again), so there is no reason to break with a 500.
        _log.exception("interrupt failed for %s", pid)
    return {"status": "interrupting"}
