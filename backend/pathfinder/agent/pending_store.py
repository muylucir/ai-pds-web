# backend/pathfinder/agent/pending_store.py — pending 질문의 S3 영속.
#
# 왜 필요한가: Strands는 세션에 pending interrupt를 함께 영속했지만(그래서
# agent._interrupt_state를 읽으면 됐다), Claude Agent SDK의 session store는
# 트랜스크립트 미러여서 pending 질문은 인메모리 Future다. GET /pending —
# 새로고침 후 질문 폼 복원 — 이 그 기능을 쓰므로 별도로 저장한다.
#
# 한 프로젝트에 pending 질문은 하나뿐이므로 키가 고정이다(프로젝트 프리픽스는
# S3Store가 붙인다).
from __future__ import annotations

import json
import logging

from pathfinder.s3store import S3StoreLike

_log = logging.getLogger("pathfinder.agent")

PENDING_KEY = "pending/questions.json"

_REQUIRED = ("interrupt_id", "questions", "sdk_questions", "session_id")


async def save_pending(s3: S3StoreLike, *, interrupt_id: str, questions: dict,
                       sdk_questions: list[dict], session_id: str) -> None:
    """sdk_questions(SDK 원형)를 함께 저장한다 — 답변을 SDK 라벨로 되번역할 때
    필요하고, 재시작 후에는 인메모리 사본이 없다."""
    await s3.put(PENDING_KEY, json.dumps({
        "interrupt_id": interrupt_id,
        "questions": questions,
        "sdk_questions": sdk_questions,
        "session_id": session_id,
    }, ensure_ascii=False))


async def load_pending(s3: S3StoreLike) -> dict | None:
    """없거나 손상됐으면 None. 500을 내지 않는다 — pending은 복원 편의이고,
    없으면 사용자가 턴을 다시 시작할 수 있다. 반쯤 복원하는 것이 더 나쁘다."""
    try:
        raw = await s3.get(PENDING_KEY)
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        _log.warning("pending payload is not valid JSON — ignoring")
        return None
    if not isinstance(data, dict) or any(k not in data for k in _REQUIRED):
        _log.warning("pending payload missing required fields — ignoring")
        return None
    return data


async def clear_pending(s3: S3StoreLike) -> None:
    """멱등 — 답변 제출과 인터럽트가 겹쳐 두 번 호출될 수 있다."""
    await s3.delete_prefix(PENDING_KEY)
