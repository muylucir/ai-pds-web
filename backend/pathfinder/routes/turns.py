# backend/pathfinder/routes/turns.py
from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from pathfinder.parsers.redaction import redact_credentials
from pathfinder.routes.deps import get_workspace
from pathfinder.sandbox.base import AgentEvent, TurnResult

router = APIRouter()

class MessageBody(BaseModel):
    text: str

def _redacted(event: AgentEvent) -> AgentEvent:
    """Return a copy of event with credential-bearing text redacted.

    Only `text` needs redacting; `path`/`kind` are structural, not
    agent-authored content.
    """
    if event.text is None:
        return event
    return event.model_copy(update={"text": redact_credentials(event.text)})

@router.post("/projects/{pid}/message")
async def post_message(pid: str, body: MessageBody):
    ws = get_workspace(pid)
    events = [_redacted(e) async for e in ws.sandbox.send_message(body.text)]
    return TurnResult(events=events)

@router.get("/projects/{pid}/events")
async def stream_events(pid: str, text: str):
    ws = get_workspace(pid)
    async def gen():
        async for event in ws.sandbox.send_message(text):
            yield {"data": _redacted(event).model_dump_json()}
    return EventSourceResponse(gen())
