# backend/aipds/agent/answer_store.py — 제출된 답변의 S3 레코드.
#
# 왜 필요한가: 히스토리 복원이 **CLI가 영어로 옮겨 적은 산문**에 의존하고 있었다.
# SDK AskUserQuestion의 tool_result는 CLI가 만드는 고정 문장이다:
#
#   Your questions have been answered: "질문"="보기 라벨", ... . You can now …
#
# session_history._cli_answer_summary는 이 문장에서 답변을 펼 수 없다 — JSON이
# 아니므로 결과가 `답변 제출: <영어 문장>` 한 줄이고, 문항 번호·보기 letter·
# 보기 텍스트가 전부 사라진다.
# 라이브 화면은 질문 payload와 답변 dict를 손에 들고 answerSummary()로 그리므로
# 같은 대화가 새로고침 전후로 다르게 보였다.
#
# 산문을 되파싱하는 대신 **답변이 도착한 순간의 정확한 값을 기록한다.** 승인
# 게이트에서 이미 같은 결정을 했다(08aaa85 "승인을 레코드로 기록 — 산문 파싱
# 의존을 끊는다"). 그 문장은 우리 것이 아니라 CLI 것이고, 질문 텍스트에 따옴표가
# 들어가면(실측: `"내부 도구라면 \"왜 …를 사서 쓰지 않고 만드는가\"가 …"`)
# 파싱이 원리적으로 모호해진다.
#
# 키는 **tool_use_id**다. SDK가 can_use_tool 콜백에 그 값을 주고(비어 있지 않음이
# 프로토콜 보장) 트랜스크립트의 tool_result도 같은 id를 들고 있으므로, 복원 시
# 조인이 정확하다 — 순서나 타임스탬프로 맞출 필요가 없다. 라운드마다 객체 하나이고
# 재제출은 같은 키를 덮는다(한 라운드의 최종 답변이 하나라는 사실과 일치).
from __future__ import annotations

import asyncio
import json
import logging

from aipds.s3store import S3StoreLike

_log = logging.getLogger("aipds.agent")

ANSWERS_PREFIX = "answers/"


def _key(tool_use_id: str) -> str:
    return f"{ANSWERS_PREFIX}{tool_use_id}.json"


def _is_valid(data: dict) -> bool:
    """복원이 곧바로 깨지는 최상위 타입만 막는다(pending_store와 같은 규율).

    answers는 {문항번호: 값} 문자열 맵이어야 한다 — 프론트의
    Record<string, string> 계약이고, answerSummary가 그 값을 letter로 해석한다.
    """
    answers = data.get("answers")
    return (
        isinstance(data.get("tool_use_id"), str) and data["tool_use_id"] != "" and
        isinstance(data.get("questions"), dict) and
        isinstance(answers, dict) and
        all(isinstance(k, str) and isinstance(v, str) for k, v in answers.items())
    )


async def save_answers(s3: S3StoreLike, *, tool_use_id: str, interrupt_id: str,
                       questions: dict, answers: dict[str, str]) -> None:
    """한 라운드의 질문 payload + 답변을 기록한다.

    questions를 함께 저장하는 것이 load-bearing이다: 답변 값은 letter("A",
    "B,C", "A: 부연")이고, 그것을 보기 텍스트로 펼치려면 그 순간의 payload가
    필요하다. 트랜스크립트의 tool_use.input에도 SDK 원형이 남지만, 그걸로
    다시 조립하면 letter 부여가 question_file_from_sdk의 그 시점 동작에
    의존하게 된다 — 사용자가 실제로 본 letter를 그대로 남기는 편이 정확하다.
    """
    await s3.put(_key(tool_use_id), json.dumps({
        "tool_use_id": tool_use_id,
        "interrupt_id": interrupt_id,
        "questions": questions,
        "answers": answers,
    }, ensure_ascii=False))


async def load_answers(s3: S3StoreLike) -> dict[str, dict]:
    """tool_use_id → 레코드. 어떤 실패도 그 레코드만 건너뛴다.

    히스토리는 보조 데이터이고(list_history의 강등과 같은 원칙) 손상된 한 건이
    나머지 라운드의 복원을 막으면 안 된다. 목록 자체가 실패하면 빈 dict —
    호출부는 레코드 없는 구 세션과 같은 경로로 떨어진다(현재 문구 유지).
    """
    try:
        keys = await s3.list(ANSWERS_PREFIX)
    except Exception:
        _log.exception("answer record listing failed")
        return {}
    # **병렬 GET.** 라운드 수에 선형이므로 순차로 읽으면 히스토리 로딩이 그만큼
    # 늦어진다(실측 2026-08-17: S3 왕복 1회 30ms). session_store.load_transcript와
    # 같은 판단이고, project_store.load_manifest가 이 리포의 선례다.
    bodies = await asyncio.gather(*(s3.get(k) for k in keys),
                                 return_exceptions=True)
    out: dict[str, dict] = {}
    for key, body in zip(keys, bodies):
        try:
            if isinstance(body, BaseException):
                raise body
            data = json.loads(body)
        except Exception:
            _log.warning("unreadable answer record skipped: %s", key)
            continue
        if not isinstance(data, dict) or not _is_valid(data):
            _log.warning("malformed answer record skipped: %s", key)
            continue
        out[data["tool_use_id"]] = data
    return out
