# backend/aipds/session_history.py
"""S3 세션 트랜스크립트 → 채팅 히스토리 변환.

위치: `discovery/transcript/{session}/main/NNNNNNNN.jsonl`
      (agent/session_store.py의 DiscoverySessionStore가 미러링)
포맷: CLI 트랜스크립트 = Anthropic Messages 모양
      {"type":"assistant","message":{"role":...,"content":[{"type":"text"|
       "tool_use"|"tool_result", ...}]}}
      message가 없는 줄(queue-operation, attachment, ai-title 등)도 섞인다.

**한 포맷만 읽는다.** 이전에는 strands 폴백 드라이버의 Bedrock Converse 모양
(`session_{pid}/agents/agent_default/messages/message_N.json`, 블록 키가
`toolUse`/`toolResult`)도 함께 읽었다. 그 드라이버를 삭제하면서 리더도 지웠다 —
그 포맷으로 남은 세션은 히스토리가 빈 목록이 된다(테스트용 세션뿐이었다).

세션 저장소는 sandbox 추상화 밖의 인프라라서 Sandbox 메서드가 아니라 이 모듈이
직접 읽는다.
"""
from __future__ import annotations
import json
import logging
from aipds.agent.answer_store import load_answers
from aipds.agent.questions_payload import (normalize_sdk_questions,
                                                question_file_from_sdk)
from aipds.agent.session_store import load_transcript
from aipds.models import HistoryItem, HistoryTraceEntry
from aipds.parsers.redaction import redact_credentials
from aipds.tool_trace import tool_detail
from aipds.s3store import S3StoreLike

_log = logging.getLogger(__name__)

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


def _cli_answer_summary(content: object) -> tuple[str, dict[str, str] | None]:
    """ask_questions tool_result 본문 → (폴백 문구, answers dict 또는 None).

    CLI가 쓰는 본문은 자기가 만든 영어 문장이라(answer_store.py 헤더) 여기서
    답변을 펼 수 없다 — 정확한 값은 `answer_records`가 준다. 이 함수는 레코드가
    없는 구 세션을 위한 폴백이다. 반환이 tuple인 것에 주의: 호출부가
    HistoryItem의 text와 answers를 함께 채운다.
    """
    if isinstance(content, list):
        inner = "".join(c.get("text", "") for c in content
                        if isinstance(c, dict))
    else:
        inner = str(content or "")
    answers = _parse_answers(inner)
    body = inner
    return _answer_fallback_text(body, answers), answers


def _is_error_result(block: dict) -> bool:
    """이 tool_result가 실패인가.

    CLI가 스키마 검증으로 막은 라운드는 `is_error: true`와 `<tool_use_error>`
    본문으로 온다(실측: 모델이 `questions`를 JSON 문자열로 넘긴 3건). 그 라운드는
    **사용자에게 질문이 뜨지도 않았으므로** 복원에서 카드를 만들면 아무도 보지
    않은 질문 카드가 유령처럼 남는다. 모델은 그 에러를 읽고 다음 턴에 제대로
    재시도하므로, 화면에는 재시도된 라운드 하나만 있어야 맞다.
    """
    if block.get("is_error") is True:
        return True
    content = block.get("content")
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return "<tool_use_error>" in text


def _sdk_questions_to_file(raw_input: object) -> dict | None:
    """tool_use.input → 프론트 QuestionFile. 실패하면 None(복원을 막지 않는다).

    라이브에서 카드를 만든 그 함수를 그대로 쓴다 — letter 부여와 Other 옵션
    추가가 사용자가 실제로 본 것과 같아야 하기 때문이다. 옵션이 하나도 없는
    질문은 ValueError이고, 그때는 카드 이름만 없는 종전 표시로 강등한다.
    """
    if not isinstance(raw_input, dict):
        return None
    questions = normalize_sdk_questions(raw_input.get("questions"))
    if not questions:
        return None
    try:
        return question_file_from_sdk(questions, name="discovery-questions")
    except ValueError:
        return None


def transform_cli_transcript(raw: list[dict], *,
                             answer_records: dict[str, dict] | None = None) -> list[HistoryItem]:
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
    records = answer_records or {}
    # 1패스: AskUserQuestion tool_use id 수집. 실제 트랜스크립트에는 Write/Read 등
    # 다른 tool_result가 섞여 있어, 답변 결과만 골라내려면 id 매칭이 필수다.
    #
    # 같은 패스에서 두 가지를 더 모은다:
    #   ask_files   id → 그 라운드의 질문 payload. tool_use.input에 SDK 원형이
    #               구조화된 채로 남아 있으므로(산문이 아니다) 카드가 "무엇을
    #               물었는지"를 되살릴 수 있다. 종전에는 이것을 버리고 카드를
    #               name=None으로 강등했다.
    #   errored     CLI가 막은 라운드. 카드를 만들지 않는다(_is_error_result).
    ask_ids: set[str] = set()
    ask_files: dict[str, dict] = {}
    errored: set[str] = set()
    for m in raw:
        msg = m.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if (block.get("type") == "tool_use"
                    and block.get("name") == "AskUserQuestion"):
                tid = str(block.get("id", ""))
                ask_ids.add(tid)
                qfile = _sdk_questions_to_file(block.get("input"))
                if qfile is not None:
                    ask_files[tid] = qfile
            elif block.get("type") == "tool_result" and _is_error_result(block):
                errored.add(str(block.get("tool_use_id", "")))

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
                    # 라이브에서는 질문 카드였다. 질문 payload를 함께 실어
                    # 보내므로 카드가 무엇을 물었는지 보여줄 수 있다.
                    #
                    # CLI가 막은 라운드는 카드를 만들지 않는다 — 사용자에게 뜬
                    # 적이 없는 질문이고, 모델이 다음 턴에 재시도한 라운드가
                    # 따로 카드를 만든다(그것이 사용자가 실제로 본 것이다).
                    tid = str(block.get("id", ""))
                    if tid in errored:
                        continue
                    qfile = ask_files.get(tid)
                    cards.append(HistoryItem(
                        role="card", card="questions",
                        name=(qfile or {}).get("name"),
                        questions=qfile))
                elif name in _CLI_FILE_TOOLS:
                    inp = block.get("input")
                    path = ""
                    if isinstance(inp, dict):
                        path = str(inp.get("file_path", ""))
                    trace.append(HistoryTraceEntry(kind="file_changed",
                                                   path=path))
                else:
                    # Read/Glob/Grep/Bash/mcp__pathfinder__* 등 — 라이브의
                    # status 이벤트와 **같은 표현**이어야 한다. 그래서 detail도
                    # 같은 모듈(tool_trace)로 뽑는다: 한쪽만 상세를 보이면
                    # 새로고침 전후로 화면이 달라진다.
                    detail = tool_detail(name, block.get("input"))
                    trace.append(HistoryTraceEntry(
                        kind="status", text=name,
                        detail=redact_credentials(detail) if detail else None))
            elif btype == "tool_result":
                tid = str(block.get("tool_use_id", ""))
                if tid in ask_ids and tid not in errored:
                    # 사용자가 실제로 답한 것 — 진행 중인 어시스턴트 턴을 먼저
                    # 닫아야 순서가 맞는다(질문 카드 다음에 답변 말풍선).
                    flush_turn()
                    # 답변 레코드가 있으면 그것이 진실이다(agent/answer_store.py):
                    # 우리가 받은 그대로의 문항번호 → letter/부연 맵과 그 순간의
                    # 질문 payload. 그러면 프론트가 라이브와 **같은 함수**로 같은
                    # 문구를 만든다. 레코드가 없는 세션(이 기능 이전)만 CLI가 쓴
                    # 영어 문장으로 강등된다 — 그것을 되파싱하지 않는 이유는
                    # 질문 텍스트에 따옴표가 들어가면 원리적으로 모호해지기
                    # 때문이다(실측 사례가 있다).
                    rec = records.get(tid)
                    if rec is not None:
                        answers = {str(k): str(v) for k, v in rec["answers"].items()}
                        items.append(HistoryItem(
                            role="user",
                            text=redact_credentials(
                                _answer_fallback_text("", answers)),
                            answers=answers,
                            questions=rec.get("questions")))
                    else:
                        text, answers = _cli_answer_summary(block.get("content"))
                        items.append(HistoryItem(role="user",
                                                 text=redact_credentials(text),
                                                 answers=answers,
                                                 questions=ask_files.get(tid)))
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


async def list_history(project_s3: S3StoreLike | None,
                       session_id: str) -> list[HistoryItem]:
    """세션 트랜스크립트를 읽어 변환. 어떤 실패도 빈 리스트로 강등한다
    (히스토리는 보조 데이터 — 화면을 막지 않는다).

    스토어가 하나인 이유: strands 폴백 드라이버를 삭제해서 읽을 프리픽스가
    `projects/{pid}/`뿐이다. 예전에는 두 스토어를 받아 어느 쪽에 내용이 있는지
    판단했다.

    project_s3가 None이면 빈 목록이다(스토어 생성 실패 — 라우트가 감싼다).
    """
    if project_s3 is None:
        return []
    try:
        cli_raw = await load_transcript(project_s3, session_id)
        if not cli_raw:
            return []
        # 답변 레코드는 트랜스크립트와 같은 프로젝트 프리픽스에 있다. 실패는
        # load_answers가 빈 dict로 강등하므로 히스토리는 레코드 없는 구 세션과
        # 같은 경로로 복원된다.
        records = await load_answers(project_s3)
        return transform_cli_transcript(cli_raw, answer_records=records)
    except Exception:
        _log.exception("transcript read failed for %s", session_id)
        return []
