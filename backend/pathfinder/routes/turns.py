# backend/pathfinder/routes/turns.py
from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from pathfinder.routes.deps import get_workspace
from pathfinder.sandbox.base import TurnResult

router = APIRouter()

class MessageBody(BaseModel):
    text: str

@router.post("/projects/{pid}/message")
async def post_message(pid: str, body: MessageBody):
    ws = get_workspace(pid)
    events = [e async for e in ws.sandbox.send_message(body.text)]
    return TurnResult(events=events)

@router.get("/projects/{pid}/events")
async def stream_events(pid: str, text: str):
    ws = get_workspace(pid)
    async def gen():
        async for event in ws.sandbox.send_message(text):
            yield {"data": event.model_dump_json()}
    return EventSourceResponse(gen())
