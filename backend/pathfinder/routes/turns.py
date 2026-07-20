# backend/pathfinder/routes/turns.py
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from pathfinder.parsers.redaction import redact_credentials
from pathfinder.routes.deps import ensure_workspace
from pathfinder.sandbox.base import AgentEvent, TurnResult

router = APIRouter()

class MessageBody(BaseModel):
    text: str

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
    events = [_redacted(e) async for e in ws.sandbox.send_message(body.text)]
    return TurnResult(events=events)

@router.get("/projects/{pid}/events")
async def stream_events(pid: str, text: str):
    ws = await ensure_workspace(pid)
    async def gen():
        async for event in ws.sandbox.send_message(text):
            yield {"data": _redacted(event).model_dump_json()}
    return EventSourceResponse(gen())

@router.get("/projects/{pid}/answers/stream")
async def stream_answers(pid: str, answers: str):
    ws = await ensure_workspace(pid)
    try:
        parsed = json.loads(answers)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="answers must be a JSON object")
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="answers must be a JSON object")
    async def gen():
        async for event in ws.sandbox.send_answers(parsed):
            yield {"data": _redacted(event).model_dump_json()}
    return EventSourceResponse(gen())

@router.get("/projects/{pid}/pending")
async def get_pending(pid: str):
    ws = await ensure_workspace(pid)
    payload = await ws.sandbox.pending()
    if payload is not None:
        payload = redact_credentials(payload)
    return {"pending": payload}
