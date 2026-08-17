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


def _is_valid(data: dict) -> bool:
    """필드 존재만으론 부족하다 — 타입이 틀리면(예: sdk_questions가 문자열)
    Task 6의 답변 되번역 경로에서 나중에 터진다. questions/sdk_questions는
    자체 구조까지 검증하진 않는다(그 정도는 파서/빌더의 책임); 여기선 답변
    되번역과 세션 조회가 곧바로 깨지는 최상위 타입만 막는다."""
    interrupt_id = data.get("interrupt_id")
    session_id = data.get("session_id")
    return (
        isinstance(interrupt_id, str) and interrupt_id != "" and
        isinstance(data.get("questions"), dict) and
        isinstance(data.get("sdk_questions"), list) and
        isinstance(session_id, str) and session_id != ""
    )


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
    if not isinstance(data, dict) or not _is_valid(data):
        _log.warning("pending payload missing or malformed required fields — ignoring")
        return None
    return data


async def clear_pending(s3: S3StoreLike) -> None:
    """멱등 — 답변 제출과 인터럽트가 겹쳐 두 번 호출될 수 있다."""
    await s3.delete_prefix(PENDING_KEY)


#: 파일 질문 라운드에서 **열려 있는 파일**의 경로. 위 PENDING_KEY와 별 키인 이유는
#: 담는 것이 완전히 다르기 때문이다 — `_is_valid`를 느슨하게 만들면 아직 살아 있는
#: AskUserQuestion 경로의 검증이 함께 약해진다.
PENDING_FILE_KEY = "pending/question-file.json"


async def save_pending_file(s3: S3StoreLike, *, file: str) -> None:
    """열려 있는 질문 파일의 경로만 저장한다.

    **질문 내용은 저장하지 않는다.** 매번 파일에서 다시 읽는 것이 세 가지를 준다:

    1. 항상 최신이다 — 에이전트가 파일을 고치면 카드가 따라간다.
    2. **clear 단계가 없다.** `runner.write_file`이 S3에 직접 쓰므로
       (runner.py:57-59) 답변이 기록되는 순간 S3가 최신이고, 미답 문항이 없으면
       복원이 자연히 None이 된다. 지울 것을 잊어 죽은 카드가 남는 경로가
       구조적으로 없다 — `disconnect`가 세 곳에서 죽은 질문을 쫓아내야 했던
       옛 경로와 다른 점이 이것이다.
    3. 퍼지 매칭이 필요 없다. 파일이 곧 정본이고 번호가 곧 키다.

    그러면 왜 경로는 저장하는가 — **모호성 때문이다.** 실측한 프로젝트에 미답
    질문 파일이 동시에 3개 있었다(답변이 유실된 결과). 스캔만으로는 어느 라운드가
    열려 있는지 알 수 없고, 틀린 카드를 보여주는 것은 안 보여주는 것보다 나쁘다.
    """
    await s3.put(PENDING_FILE_KEY, json.dumps({"file": file},
                                              ensure_ascii=False))


async def load_pending_file(s3: S3StoreLike) -> str | None:
    """열려 있는 질문 파일의 경로. 없거나 손상됐으면 None.

    500을 내지 않는 이유는 `load_pending`과 같다 — 복원은 편의이고, 없으면
    사용자가 턴을 다시 시작할 수 있다."""
    try:
        raw = await s3.get(PENDING_FILE_KEY)
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        _log.warning("pending question-file payload is not valid JSON — ignoring")
        return None
    file = data.get("file") if isinstance(data, dict) else None
    if not isinstance(file, str) or not file:
        _log.warning("pending question-file payload has no usable path — ignoring")
        return None
    return file
