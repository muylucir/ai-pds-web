# backend/pathfinder/session_history.py
"""S3 strands 세션 메시지 → 채팅 히스토리 변환.

세션 저장소는 sandbox 추상화 밖의 인프라(strands SDK가 쓰는 S3 오브젝트)라서
Sandbox 메서드가 아니라 이 모듈이 직접 읽는다. VM은 절대 부팅하지 않는다.
"""
from __future__ import annotations
import json
import logging
import re
from pathfinder.models import HistoryItem
from pathfinder.parsers.redaction import redact_credentials
from pathfinder.sandbox.s3store import S3StoreLike

_log = logging.getLogger(__name__)
_MSG_KEY = re.compile(r"message_(\d+)\.json$")


def transform_messages(raw: list[dict]) -> list[HistoryItem]:
    # 1패스: ask_questions toolUse id 수집 (답변 toolResult 식별용 — 실 세션에는
    # file_write 등 다른 toolResult가 섞여 있어 이름 매칭이 필수다).
    ask_ids: set[str] = set()
    for m in raw:
        for block in m.get("message", {}).get("content", []):
            tu = block.get("toolUse")
            if tu and tu.get("name") == "ask_questions":
                ask_ids.add(tu.get("toolUseId", ""))

    items: list[HistoryItem] = []
    for m in raw:
        msg = m.get("message", {})
        role = msg.get("role")
        texts: list[str] = []
        cards: list[HistoryItem] = []
        for block in msg.get("content", []):
            if "text" in block:
                texts.append(block["text"])
            elif "toolUse" in block:
                tu = block["toolUse"]
                if tu.get("name") == "ask_questions":
                    name = (tu.get("input", {}).get("questions_file") or {}).get("name")
                    cards.append(HistoryItem(role="card", card="questions", name=name))
            elif "toolResult" in block:
                tr = block["toolResult"]
                if tr.get("toolUseId") in ask_ids:
                    inner = "".join(c.get("text", "") for c in tr.get("content", []))
                    # 도구 결과 원문("사용자 답변: {...}")에서 답변부만 살린 요약.
                    # 답변이 JSON 객체면 사람이 읽을 "번호: 값 · ..." 형태로 —
                    # raw JSON을 사용자 말풍선에 그대로 노출하지 않는다.
                    answer = inner.replace("사용자 답변: ", "", 1)
                    try:
                        parsed = json.loads(answer)
                        if isinstance(parsed, dict) and parsed:
                            pretty = " · ".join(
                                f"{k}: {v}" for k, v in sorted(
                                    parsed.items(), key=lambda kv: str(kv[0])))
                            summary = f"답변 제출 — {pretty}"
                        else:
                            summary = f"답변 제출: {answer}"
                    except (json.JSONDecodeError, TypeError):
                        summary = f"답변 제출: {answer}"
                    items.append(HistoryItem(
                        role="user", text=redact_credentials(summary)))
            # reasoningContent 및 기타 블록은 생략
        if texts:
            joined = redact_credentials("\n".join(texts))
            items.append(HistoryItem(role="ai" if role == "assistant" else "user",
                                     text=joined))
        items.extend(cards)
    return items


async def list_history(s3: S3StoreLike, session_id: str) -> list[HistoryItem]:
    """세션의 message_*.json을 message_id 순으로 읽어 변환. 어떤 실패도
    빈 리스트로 강등(히스토리는 보조 데이터 — 화면을 막지 않는다)."""
    prefix = f"session_{session_id}/agents/agent_default/messages/"
    try:
        keys = await s3.list(prefix)
        numbered: list[tuple[int, str]] = []
        for k in keys:
            match = _MSG_KEY.search(k)
            if match:
                numbered.append((int(match.group(1)), k))
        raw = []
        for _, key in sorted(numbered):
            raw.append(json.loads(await s3.get(key)))
        return transform_messages(raw)
    except Exception:
        _log.exception("history read failed for %s", session_id)
        return []
