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

#: 구 트랜스크립트의 접두사. 이미 S3에 있는 대화가 이것을 쓰므로 영구히
#: 받아 준다 — 지우면 진행 중인 워크숍의 히스토리가 빈 말풍선이 된다.
#: parsers/audit.py가 `사용자 입력|User Raw Input`을 둘 다 받는 것과 같은 규율.
_LEGACY_ANSWER_PREFIX = "사용자 답변: "


def _strip_answer_prefix(raw: str) -> str:
    """ask_questions tool_result 본문에서 접두사를 벗긴다. 신·구 두 형태."""
    # 함수 안에서 임포트한다 — strands_tools는 모듈 최상단에서 `strands`를
    # 끌어오므로, 이 모듈(히스토리 복원)이 그 SDK에 의존하게 만들지 않는다.
    from pathfinder.agent.strands_tools import ANSWER_PREFIX
    for prefix in (ANSWER_PREFIX, _LEGACY_ANSWER_PREFIX):
        if raw.startswith(prefix):
            return raw[len(prefix):]
    return raw


def _parse_answers(raw: str) -> dict[str, str] | None:
    """접두사를 벗긴 본문 → 답변 dict, 펼 수 없으면 None.

    None은 자유 서술 답변(JSON이 아닌 것)이다. 그때는 호출부가 text 폴백만
    채운다 — 프론트가 dict 없이도 말풍선을 그릴 수 있어야 한다.
    """
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict) or not parsed:
        return None
    # 값이 문자열이 아닌 경우(에이전트가 숫자를 넣는 등)도 문자열로 통일한다 —
    # 프론트의 Record<string, string> 계약을 지킨다.
    return {str(k): str(v) for k, v in parsed.items()}


def _answer_fallback_text(body: str, answers: dict[str, str] | None) -> str:
    """answers를 모르는 소비자를 위한 한국어 폴백 문구.

    사람이 읽는 최종 문구는 프론트가 UI 언어로 만든다(HistoryItem.answers).
    여기 남은 한국어는 그 필드를 모르는 구 프론트가 빈 말풍선을 띄우지 않게
    하는 안전망일 뿐이다 — 새 프론트는 이 값을 무시한다.
    """
    if answers:
        pretty = " · ".join(f"{k}: {v}" for k, v in
                            sorted(answers.items(), key=lambda kv: str(kv[0])))
        return f"답변 제출 — {pretty}"
    return f"답변 제출: {body}"

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
                    body = _strip_answer_prefix(inner)
                    answers = _parse_answers(body)
                    # text는 폴백으로 계속 채운다 — answers를 모르는 구
                    # 프론트가 빈 말풍선을 띄우지 않게 한다. 사람이 읽는 문구는
                    # answers가 있으면 프론트가 UI 언어로 다시 만든다.
                    items.append(HistoryItem(
                        role="user",
                        text=redact_credentials(_answer_fallback_text(body, answers)),
                        answers=answers))
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


def _cli_answer_summary(content: object) -> tuple[str, dict[str, str] | None]:
    """ask_questions tool_result 본문 → (폴백 문구, answers dict 또는 None).

    Converse 경로(transform_messages)와 같은 규칙을 쓴다: 접두사를 벗기고
    (신·구 두 형태) JSON이면 dict로 편다. 반환이 tuple로 바뀐 것에 주의 —
    호출부가 HistoryItem의 text와 answers를 함께 채운다.
    """
    if isinstance(content, list):
        inner = "".join(c.get("text", "") for c in content
                        if isinstance(c, dict))
    else:
        inner = str(content or "")
    body = _strip_answer_prefix(inner)
    answers = _parse_answers(body)
    return _answer_fallback_text(body, answers), answers


def transform_cli_transcript(raw: list[dict]) -> list[HistoryItem]:
    """CLI 트랜스크립트(Anthropic Messages 모양) → 채팅 히스토리.

    라이브 스트림(claude_driver._translate)과 같은 표현을 만드는 것이 목표다 —
    text는 말풍선, 도구 실행은 트레이스, AskUserQuestion은 카드. 그래야
    스크롤백이 방금 본 화면과 달라 보이지 않는다.

    message가 없는 줄(queue-operation, attachment, ai-title, last-prompt 등)은
    건너뛴다. 그 줄들은 CLI의 내부 부기이고 대화 내용이 아니다.

    **한 턴은 말풍선 하나다.** CLI는 도구를 부를 때마다 별도 assistant 줄을 쓰므로
    (실측한 5회 도구 사용 턴: thinking 1줄 + tool_use 5줄 + text 1줄), 줄마다
    항목을 만들면 라이브에서 말풍선 하나였던 턴이 7개로 쪼개지고 그중 대부분이
    텍스트 없는 빈 말풍선이 된다 — 화면에서는 "추론 과정"만 달린 빈 회색 상자가
    줄줄이 나온다. 라이브는 턴 하나당 AiItem 하나를 만들어 message를 그 하나에
    누적하고 도구는 같은 항목의 trace에 쌓으므로(useTurnStream.ts:108,121-123),
    복원도 그렇게 모은다.

    턴 경계는 **실제 사용자 발화**다. `tool_result`만 담은 user 줄은 사용자가 한
    말이 아니라 도구 실행 결과이고, 라이브는 그 줄을 아무것도 렌더하지 않는다 —
    경계로 취급하면 도구 호출 하나하나가 다시 턴이 되어 원래 문제로 돌아간다.
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
    # 진행 중인 어시스턴트 턴. 실제 사용자 발화를 만나거나 트랜스크립트가 끝날
    # 때 하나의 항목으로 확정된다.
    turn_texts: list[str] = []
    turn_trace: list[HistoryTraceEntry] = []
    turn_cards: list[HistoryItem] = []

    def flush_turn() -> None:
        """모인 어시스턴트 턴을 항목 하나(+ 뒤따르는 카드)로 확정한다.

        텍스트가 없고 트레이스만 있어도 말풍선을 만든다 — 중단된 턴(유휴
        타임아웃, SSE 끊김)이 그 모양이고, 도구를 무엇까지 돌렸는지가 스크롤백에
        남을 유일한 기록이다. 라이브에서 그 자리에 있던 진행 표시는 복원 대상이
        아니다.

        카드는 말풍선 **뒤**에 붙는다. 라이브에서는 모델이 "왜 묻는지" 설명을
        먼저 흘리고 그 다음 질문 카드가 뜨므로(claude_driver의 _CONTACT_ADDENDUM이
        그 설명을 요구한다), 카드를 즉시 내보내면 말풍선 확정이 미뤄지는 사이
        순서가 뒤집혀 설명 없는 질문이 먼저 나온다.
        """
        nonlocal turn_texts, turn_trace, turn_cards
        if turn_texts or turn_trace:
            items.append(HistoryItem(
                role="ai",
                text=redact_credentials("\n".join(turn_texts)) if turn_texts else "",
                trace=turn_trace))
        items.extend(turn_cards)
        turn_texts, turn_trace, turn_cards = [], [], []

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
                    # 사용자가 실제로 답한 것 — 진행 중인 어시스턴트 턴을 먼저
                    # 닫아야 순서가 맞는다(질문 카드 다음에 답변 말풍선).
                    flush_turn()
                    text, answers = _cli_answer_summary(block.get("content"))
                    items.append(HistoryItem(role="user",
                                             text=redact_credentials(text),
                                             answers=answers))
            # thinking/redacted_thinking 등은 생략 — 복원해 보여줄 내용이 아니다.

        if role == "assistant":
            # 턴에 누적한다. 도구 호출마다 오는 별도 줄이 각자 말풍선이 되지
            # 않도록, 확정은 다음 사용자 발화(또는 트랜스크립트 끝)로 미룬다.
            turn_texts.extend(texts)
            turn_trace.extend(trace)
            turn_cards.extend(cards)
            continue

        # user 줄. 실제 발화만 턴 경계다 — tool_result만 담은 줄은 도구 실행
        # 결과이고 라이브는 그것을 렌더하지 않는다(위 tool_result 분기가 답변
        # 말풍선을 이미 만들었다).
        if texts:
            flush_turn()
            items.append(HistoryItem(role="user",
                                     text=redact_credentials("\n".join(texts))))
        items.extend(cards)

    flush_turn()
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
