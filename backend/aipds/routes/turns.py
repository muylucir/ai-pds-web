# backend/aipds/routes/turns.py
import logging
from typing import AsyncIterator, Callable
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from aipds.parsers.redaction import redact_credentials
import aipds.app as app_module
from aipds.routes.deps import ensure_workspace
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
               events: Callable[[], AsyncIterator[AgentEvent]]) -> TurnJob:
    """턴을 서버 작업으로 시작한다. 도는 턴이 있으면 409 — 본문에 그 턴의 id가 있다.

    409에 id를 싣는 이유: 거절된 쪽이 할 일은 "다시 보내기"가 아니라 **이미 도는
    턴을 보기**다. 이 프로젝트의 턴은 하나뿐이므로 그것이 사용자가 기다리던 턴이다.

    다른 라우트(답변 파일, 승인)도 이것으로 턴을 연다 — 409의 모양이 경로마다
    달라지지 않게.
    """
    try:
        return ws.turns.start(kind, events)
    except TurnBusy as busy:
        raise HTTPException(status_code=409, detail={
            "code": "turn_in_progress", "turn_id": busy.job.id})


def ensure_idle(ws: Workspace) -> None:
    """도는 턴이 있으면 `start_turn`과 같은 409를 **부수 효과 전에** 낸다.

    답변 파일 쓰기나 승인 레코드처럼 턴 앞에 기록이 있는 경로가 쓴다 — 기록만 남고
    턴은 거절되면, 사용자는 반영된 것처럼 보이는 답이 에이전트에 닿지 않은 상태를
    보게 된다.
    """
    running = ws.turns.current()
    if running is not None:
        raise HTTPException(status_code=409, detail={
            "code": "turn_in_progress", "turn_id": running.id})


def turn_response(job: TurnJob, after: int = 0) -> EventSourceResponse:
    """턴 로그를 `after` 뒤부터 SSE로 흘린다. 프레임의 `id`가 seq다.

    구독자가 끊겨도 턴은 계속 돈다 — 이 generator는 읽기만 한다. 다시 붙을 때는
    마지막으로 받은 seq를 `after`로 준다.
    """
    async def gen():
        async for seq, event in subscribe(job, after):
            yield {"id": str(seq),
                   "data": _redacted(event).model_dump_json()}
    return EventSourceResponse(gen())


def _job_or_404(ws: Workspace, turn: str) -> TurnJob:
    job = ws.turns.get(turn)
    if job is None:
        # 끝난 지 오래됐거나(보존 시간), 재시작 전의 턴이거나, 다른 프로젝트의 id다.
        # 화면은 이때 `GET /history`로 떨어진다.
        raise HTTPException(status_code=404, detail="unknown turn")
    return job


@router.post("/projects/{pid}/turns")
async def create_turn(pid: str, body: MessageBody):
    """턴을 **시작**하고 id를 돌려준다. 스트림은 `GET /events?turn=<id>`로 본다.

    텍스트를 본문으로 받는 이유: EventSource는 GET만 지원하고, 긴 입력(특히 한글)을
    URL에 실으면 요청 라인이 커져 프록시가 431을 낸다(frontend lib/api/sse.ts의
    createTurn에 실측이 있다). 워크스페이스를 여기서 확인해 없는 프로젝트는 404로
    끝낸다.
    """
    ws = await ensure_workspace(pid)
    job = start_turn(ws, "message", lambda: ws.runner.send_message(body.text))
    return {"turn_id": job.id}


@router.get("/projects/{pid}/events")
async def stream_events(pid: str, turn: str, after: int = 0):
    """턴 하나를 본다. `after`는 이미 받은 마지막 seq다(처음이면 0).

    턴은 `POST /turns`·`POST /answers`·파일 질문 답변·승인이 시작한다 — 이 경로는
    보기만 한다. 답변 턴도 같은 경로로 본다: 턴은 종류가 아니라 id로 구별된다.
    """
    ws = await ensure_workspace(pid)
    return turn_response(_job_or_404(ws, turn), after)


@router.get("/projects/{pid}/turn")
async def get_turn(pid: str):
    """이 프로젝트의 현재(또는 마지막) 턴. 화면은 열릴 때 이것으로 붙을지 정한다.

    메모리에 턴이 없는데 S3 표식이 `running`이면 그 턴은 백엔드 재시작으로 끊긴
    것이다 — `interrupted`로 알린다(turn_marker.py 헤더).
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
        return None     # 로컬 개발: 표식을 쓰는 S3가 없다
    try:
        return await load_marker(app_module.s3_store_factory(pid))
    except Exception:
        _log.exception("turn marker read failed")
        return None


@router.post("/projects/{pid}/answers")
async def create_answers_turn(pid: str, body: AnswersBody):
    """답변 제출로 턴을 시작한다. `/turns`와 같은 이유로 본문으로 받는다 — 자유 서술
    답변이 길면 같은 URL 길이 한도에 걸린다."""
    ws = await ensure_workspace(pid)
    job = start_turn(ws, "answers", lambda: ws.runner.send_answers(body.answers))
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
