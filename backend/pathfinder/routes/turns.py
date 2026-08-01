# backend/pathfinder/routes/turns.py
import json
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from pathfinder.parsers.redaction import redact_credentials
from pathfinder.routes.deps import ensure_workspace
from pathfinder.models import AgentEvent, TurnResult

router = APIRouter()
_log = logging.getLogger(__name__)

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
    events = [_redacted(e) async for e in ws.runner.send_message(body.text)]
    return TurnResult(events=events)

@router.get("/projects/{pid}/events")
async def stream_events(pid: str, text: str):
    ws = await ensure_workspace(pid)
    async def gen():
        async for event in ws.runner.send_message(text):
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
        async for event in ws.runner.send_answers(parsed):
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
    """진행 중인 턴을 중단한다. 프로토타입 쪽
    (/prototypes/{slug}/interrupt)과 같은 계약이다.

    진행 중인 턴이 없어도 202: 중단은 멱등이고, 사용자가 반응이 없다고 다시
    누르는 것이 정상 경로다. 202(Accepted)인 이유는 실제 중단이 서브프로세스
    왕복이라 이 응답 시점에 끝나 있지 않다는 것 — 결과는 SSE 스트림이 종결
    이벤트로 알린다.
    """
    ws = await ensure_workspace(pid)   # 없는 프로젝트는 404
    try:
        await ws.runner.interrupt()
    except Exception:
        # 사용자가 중단을 요청했는데 실제로는 안 먹혔다는 뜻이라 로그로는
        # 남긴다 — 하지만 이 docstring이 약속하는 202/멱등 계약은 지킨다.
        # 프론트는 실패를 그냥 삼키므로(다시 누르면 됨) 500으로 깨질 이유가 없다.
        _log.exception("interrupt failed for %s", pid)
    return {"status": "interrupting"}
