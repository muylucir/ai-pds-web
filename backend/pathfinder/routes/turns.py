# backend/pathfinder/routes/turns.py
import json
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from pathfinder.parsers.redaction import redact_credentials
import pathfinder.app as app_module
from pathfinder.routes.deps import ensure_workspace
from pathfinder.models import AgentEvent, TurnResult

router = APIRouter()
_log = logging.getLogger(__name__)

class MessageBody(BaseModel):
    text: str


class AnswersBody(BaseModel):
    answers: dict[str, str]


def _turn_payload(pid: str, handle: str | None, inline: object,
                  key: str) -> object:
    """핸들 또는 인라인 쿼리 파라미터에서 턴 입력을 꺼낸다.

    핸들 경로가 기본이다: 긴 입력이 URL에 실리면 요청 라인이 커져 프록시가
    431을 낸다(pathfinder/turn_handles.py 헤더의 실측). 인라인 경로를 남겨
    두는 이유는 배포가 원자적이지 않다는 것 — 백엔드가 먼저 올라간 순간
    구 프론트가 여전히 ?text=/?answers=로 보낸다.

    둘 다 없으면 400이다. 조용히 빈 턴을 돌리면 사용자는 아무 응답 없는
    말풍선을 보고 원인을 알 수 없다.
    """
    if handle is not None:
        payload = app_module.turn_handles.consume(pid, handle)
        if payload is None:
            # 만료·재사용·다른 프로젝트 — 어느 쪽인지 구별해 알려주지 않는다
            # (핸들의 존재 여부가 정보가 되지 않게).
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
    """턴 텍스트를 **본문**으로 받아 짧은 핸들을 돌려준다.

    EventSource는 GET만 지원해 본문을 실을 수 없으므로, 긴 입력을 URL에서
    빼는 유일한 방법이 이 2단계다(pathfinder/turn_handles.py 헤더 참조).
    워크스페이스를 여기서 확인해 없는 프로젝트는 404로 끝낸다 — 핸들만 받고
    스트림에서 404가 나면 사용자는 "연결이 끊어졌습니다"만 본다.
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

@router.post("/projects/{pid}/answers")
async def create_answers_turn(pid: str, body: AnswersBody):
    """답변 제출의 핸들 발급. `/turns`와 같은 이유다 — 자유 서술 답변이 길면
    같은 URL 길이 한도에 걸린다."""
    await ensure_workspace(pid)
    return {"turn_id": app_module.turn_handles.create(pid,
                                                      {"answers": body.answers})}


@router.get("/projects/{pid}/answers/stream")
async def stream_answers(pid: str, turn: str | None = None,
                         answers: str | None = None):
    ws = await ensure_workspace(pid)
    raw = _turn_payload(pid, turn, answers, "answers")
    # 핸들 경로는 이미 dict다(POST 본문이 검증했다). 인라인 경로만 파싱한다.
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
