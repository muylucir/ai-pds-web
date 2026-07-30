# backend/pathfinder/session_history.py
"""S3 세션 트랜스크립트 → 채팅 히스토리 변환.

**두 포맷을 읽는다.** Discovery 드라이버가 교체되면서 저장 위치와 포맷이 모두
바뀌었고, 그때 이 모듈이 함께 옮겨오지 않아 히스토리 복원이 조용히 깨졌다
(빈 목록 → 화면에 아무것도, 에러도 없음).

  claude (현재 기본, ClaudeDriver)
    위치: discovery/transcript/{session}/main/NNNNNNNN.jsonl
          (agent/session_store.py의 DiscoverySessionStore가 미러링)
    포맷: CLI 트랜스크립트 = Anthropic Messages 모양
          {"type":"assistant","message":{"role":...,"content":[{"type":"text"|
           "tool_use"|"tool_result", ...}]}}
          message가 없는 줄(queue-operation, attachment, ai-title 등)도 섞인다.

  strands (PATHFINDER_DISCOVERY_DRIVER=strands 폴백)
    위치: session_{pid}/agents/agent_default/messages/message_N.json
    포맷: Bedrock Converse 모양 — content 블록의 키가 `toolUse`/`toolResult`
          (camelCase, "type" 필드 없음)

두 포맷을 한 함수로 합치지 않은 이유는 블록 판별 방식이 다르다는 것이다 —
Converse는 키의 존재로("toolUse" in block), Anthropic은 block["type"] 값으로
구분한다. 하나의 분기 더미로 만들면 어느 쪽 계약도 읽어낼 수 없게 된다.

세션 저장소는 sandbox 추상화 밖의 인프라라서 Sandbox 메서드가 아니라 이 모듈이
직접 읽는다. VM은 절대 부팅하지 않는다.
"""
from __future__ import annotations
import asyncio
import json
import logging
import re
from pathfinder.agent.session_store import load_transcript
from pathfinder.models import HistoryItem, HistoryTraceEntry
from pathfinder.parsers.redaction import redact_credentials
from pathfinder.s3store import S3StoreLike

_log = logging.getLogger(__name__)
_MSG_KEY = re.compile(r"message_(\d+)\.json$")

#: 라이브 이벤트에서 file_changed로 표현되는 도구(claude_driver._FILE_TOOLS와
#: 같은 집합). 히스토리도 같은 표현이어야 스크롤백이 라이브와 달라 보이지 않는다.
_CLI_FILE_TOOLS = {"Write", "Edit", "MultiEdit"}


def _questions_file_name(tool_input: object) -> str | None:
    """ask_questions 인자의 questions_file에서 name을 뽑는다.

    Strands/LLM이 이 인자를 dict로 넘길 때도, 직렬화된 JSON '문자열'로 넘길
    때도 있다(실 세션에서 둘 다 관측됨). 문자열이면 JSON으로 파싱해 dict로
    정규화하고, dict가 아니거나 파싱이 실패하면 name=None으로 강등한다 —
    이 한 블록의 형태 이상이 transform_messages 전체를 죽여 히스토리를 통째로
    []로 만들면 안 된다(fallback 원칙)."""
    qf: object = None
    if isinstance(tool_input, dict):
        qf = tool_input.get("questions_file")
    if isinstance(qf, str):
        try:
            qf = json.loads(qf)
        except (json.JSONDecodeError, TypeError):
            return None
    if isinstance(qf, dict):
        return qf.get("name")
    return None


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
        trace: list[HistoryTraceEntry] = []
        for block in msg.get("content", []):
            if "text" in block:
                texts.append(block["text"])
            elif "toolUse" in block:
                tu = block["toolUse"]
                if tu.get("name") == "ask_questions":
                    name = _questions_file_name(tu.get("input"))
                    cards.append(HistoryItem(role="card", card="questions", name=name))
                elif tu.get("name") in ("file_write", "file_append"):
                    # 라이브 file_changed 이벤트와 동일한 표현 — 경로만 노출
                    trace.append(HistoryTraceEntry(
                        kind="file_changed",
                        path=str(tu.get("input", {}).get("path", ""))))
                else:
                    # file_read/report_stage/submit_document 등 — 라이브의
                    # status 이벤트(도구 이름)와 동일한 표현
                    trace.append(HistoryTraceEntry(
                        kind="status", text=tu.get("name", "")))
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
            # reasoningContent 및 기타 블록은 생략 (실 세션의 reasoning text는
            # 비어 있고 signature만 있음 — 복원할 내용 자체가 없다)
        if texts or (role == "assistant" and trace):
            # 텍스트 없이 도구만 부른 어시스턴트 턴도 라이브에서는 트레이스가
            # 붙은 (빈) 말풍선이었다 — 트레이스를 잃지 않도록 빈 텍스트로 생성.
            joined = redact_credentials("\n".join(texts)) if texts else ""
            items.append(HistoryItem(
                role="ai" if role == "assistant" else "user",
                text=joined,
                trace=trace if role == "assistant" else []))
        items.extend(cards)
    return items


def _cli_answer_summary(content: object) -> str:
    """ask_questions tool_result 본문 → 사람이 읽는 답변 요약.

    Converse 경로와 같은 규칙을 쓴다(위 transform_messages 참조): 원문
    "사용자 답변: {...}"에서 답변부만 남기고, JSON 객체면 "번호: 값 · ..."로
    편다 — raw JSON을 사용자 말풍선에 그대로 노출하지 않는다.
    """
    if isinstance(content, list):
        inner = "".join(c.get("text", "") for c in content
                        if isinstance(c, dict))
    else:
        inner = str(content or "")
    answer = inner.replace("사용자 답변: ", "", 1)
    try:
        parsed = json.loads(answer)
    except (json.JSONDecodeError, TypeError):
        return f"답변 제출: {answer}"
    if isinstance(parsed, dict) and parsed:
        pretty = " · ".join(f"{k}: {v}" for k, v in
                            sorted(parsed.items(), key=lambda kv: str(kv[0])))
        return f"답변 제출 — {pretty}"
    return f"답변 제출: {answer}"


def transform_cli_transcript(raw: list[dict]) -> list[HistoryItem]:
    """CLI 트랜스크립트(Anthropic Messages 모양) → 채팅 히스토리.

    라이브 스트림(claude_driver._translate)과 같은 표현을 만드는 것이 목표다 —
    text는 말풍선, 도구 실행은 트레이스, AskUserQuestion은 카드. 그래야
    스크롤백이 방금 본 화면과 달라 보이지 않는다.

    message가 없는 줄(queue-operation, attachment, ai-title, last-prompt 등)은
    건너뛴다. 그 줄들은 CLI의 내부 부기이고 대화 내용이 아니다.
    """
    # 1패스: AskUserQuestion tool_use id 수집. 실제 트랜스크립트에는 Write/Read 등
    # 다른 tool_result가 섞여 있어, 답변 결과만 골라내려면 id 매칭이 필수다.
    ask_ids: set[str] = set()
    for m in raw:
        msg = m.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if (isinstance(block, dict) and block.get("type") == "tool_use"
                    and block.get("name") == "AskUserQuestion"):
                ask_ids.add(str(block.get("id", "")))

    items: list[HistoryItem] = []
    for m in raw:
        msg = m.get("message")
        if not isinstance(msg, dict):
            continue  # 부기 줄 — 복원할 대화가 없다
        role = msg.get("role")
        content = msg.get("content")
        # 첫 user 줄의 content는 리스트가 아니라 평문 문자열일 수 있다(실측:
        # {"role":"user","content":"Say OK"}). 블록 루프가 문자열을 문자 단위로
        # 훑지 않도록 정규화한다.
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            continue

        texts: list[str] = []
        cards: list[HistoryItem] = []
        trace: list[HistoryTraceEntry] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = block.get("text")
                if text:
                    texts.append(str(text))
            elif btype == "tool_use":
                name = str(block.get("name", ""))
                if name == "AskUserQuestion":
                    # 라이브에서는 질문 카드였다. 카드에 붙일 이름은 payload
                    # 빌더가 정하는 값과 같게 둔다(builder/driver 모두
                    # name="...-questions"로 만든다) — 없으면 None으로 강등.
                    cards.append(HistoryItem(role="card", card="questions",
                                             name=None))
                elif name in _CLI_FILE_TOOLS:
                    inp = block.get("input")
                    path = ""
                    if isinstance(inp, dict):
                        path = str(inp.get("file_path", ""))
                    trace.append(HistoryTraceEntry(kind="file_changed",
                                                   path=path))
                else:
                    # Read/Glob/Grep/Bash/mcp__pathfinder__* 등 — 라이브의
                    # status 이벤트(도구 이름)와 같은 표현.
                    trace.append(HistoryTraceEntry(kind="status", text=name))
            elif btype == "tool_result":
                if str(block.get("tool_use_id", "")) in ask_ids:
                    items.append(HistoryItem(
                        role="user",
                        text=redact_credentials(
                            _cli_answer_summary(block.get("content")))))
            # thinking/redacted_thinking 등은 생략 — 복원해 보여줄 내용이 아니다.

        if texts or (role == "assistant" and trace):
            # 텍스트 없이 도구만 부른 어시스턴트 턴도 라이브에서는 트레이스가
            # 붙은 (빈) 말풍선이었다 — 트레이스를 잃지 않도록 빈 텍스트로 생성.
            joined = redact_credentials("\n".join(texts)) if texts else ""
            items.append(HistoryItem(
                role="ai" if role == "assistant" else "user",
                text=joined,
                trace=trace if role == "assistant" else []))
        items.extend(cards)
    return items


async def list_history(s3: S3StoreLike | None, session_id: str, *,
                       project_s3: S3StoreLike | None = None) -> list[HistoryItem]:
    """세션 히스토리를 읽어 변환. 어떤 실패도 빈 리스트로 강등한다(히스토리는
    보조 데이터 — 화면을 막지 않는다).

    두 스토어를 받는 이유는 두 드라이버가 서로 다른 S3 프리픽스에 쓰기 때문이다
    (routes/history.py의 헤더 참조):

      project_s3 (projects/{pid}/)  claude 드라이버의 트랜스크립트 미러
      s3         (sessions/)        strands S3SessionManager의 메시지

    claude 쪽을 먼저 본다 — 현재 기본 드라이버가 쓰는 곳이다. 비어 있으면
    strands로 폴백한다: 드라이버를 되돌렸거나(PATHFINDER_DISCOVERY_DRIVER=strands)
    교체 전에 만들어진 세션이 그쪽에 있다. 둘 다 내용이 있는 세션은 없지만,
    있다면 현재 드라이버가 쓰는 쪽이 최신이므로 이 순서가 맞다.

    project_s3가 None이면 claude 경로를 건너뛴다(스토어 생성 실패 — 라우트가
    한쪽만 넘길 수 있다).
    """
    if project_s3 is not None:
        try:
            cli_raw = await load_transcript(project_s3, session_id)
            if cli_raw:
                return transform_cli_transcript(cli_raw)
        except Exception:
            # strands 폴백을 여전히 시도한다 — 한쪽 포맷의 실패가 다른 쪽에서
            # 복원 가능한 히스토리를 가리면 안 된다.
            _log.exception("cli transcript read failed for %s", session_id)

    if s3 is None:
        return []
    prefix = f"session_{session_id}/agents/agent_default/messages/"
    try:
        keys = await s3.list(prefix)
        numbered: list[tuple[int, str]] = []
        for k in keys:
            match = _MSG_KEY.search(k)
            if match:
                numbered.append((int(match.group(1)), k))
        # 병렬 GET — 순차(개수 × S3 왕복 ≈ 80개면 ~5초)로 읽으면 /history가
        # 화면 로딩을 수 초씩 막는다. gather는 입력 순서대로 결과를 돌려주므로
        # message_id 정렬은 그대로 유지된다.
        bodies = await asyncio.gather(
            *(s3.get(key) for _, key in sorted(numbered)))
        raw = [json.loads(b) for b in bodies]
        return transform_messages(raw)
    except Exception:
        _log.exception("history read failed for %s", session_id)
        return []
