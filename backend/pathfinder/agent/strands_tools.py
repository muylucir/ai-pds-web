# backend/pathfinder/agent/strands_tools.py — StrandsDriver(driver.py)가 쓰는
# 6개짜리 구(舊) 도구 세트. Task 5에서 tools.py를 Claude Agent SDK 방식 2개로
# 줄이면서 여기로 그대로 옮겼다(로직 변경 없음, strands `@tool` 그대로).
#
# 왜 별도 파일인가: driver.py(=StrandsDriver, 롤백 경로)는 이 이관 작업 중
# 손대지 않기로 되어 있고, driver.py는 `build_tools(workspace, rules_dir, emit)`
# 3-인자 시그니처와 strands 플레이버 도구 객체(.tool_name 등)를 그대로 기대한다.
# tools.py의 build_tools는 Claude Agent SDK 도구 두 개(report_stage,
# submit_document)만 돌려주는 2-인자 함수로 바뀌므로 시그니처와 도구 개수가
# 모두 달라 driver.py가 더는 그것을 쓸 수 없다 — 두 SDK의 `@tool` 데코레이터도
# 서로 다른 타입을 만들어 섞어 쓸 수 없다. 그래서 driver.py가 기존 그대로
# 계속 동작하도록 이 파일을 새로 만들고 driver.py의 import 한 줄만 옮겼다.
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Any, Callable
from strands import tool
from pathfinder.models import AgentEvent
from pathfinder.agent.state_sync import upsert_stage
from pathfinder.agent.questions_payload import normalize_questions_payload
from pathfinder.agent.discovery_guard import write_denial
from pathfinder.agent.question_file_answers import record_answers

_log = logging.getLogger("pathfinder.agent")

#: ask_questions tool_result의 접두사. session_history._strip_answer_prefix가
#: 같은 값을 벗긴다 — 두 곳이 어긋나면 채팅 히스토리의 답변 말풍선이 깨진다.
#: 언어 중립인 이유: 이 문자열은 사용자에게 보이지 않고(프론트가 UI 언어로
#: 문구를 만든다) 파싱 계약일 뿐이다.
ANSWER_PREFIX = "[answers] "

QUESTIONS_SCHEMA_HINT = (
    "ask_questions의 questions_file 인자는 반드시 다음 JSON 형태여야 한다: "
    '{"name": str, "preamble": str|null, "parse_ok": true, "raw_markdown": null, '
    '"questions": [{"number": int, "category": str|null, "text": str, "answer": null, '
    '"multi_select": bool, "options": [{"letter": "A".."F"|"X", "text": str, '
    '"is_other": bool, "recommended": bool}]}]}. '
    "options 규칙(반드시 지킬 것): 실질 보기는 A부터 순서대로 letter를 매기고 "
    "is_other=false로 둔다. 자유 입력 보기는 **정확히 하나**, 목록 맨 끝에 "
    'letter="X" + is_other=true로 둔다. 실질 보기에 is_other=true를 주면 그 보기의 '
    "텍스트가 화면에서 사라지므로 절대 금지한다. 한글은 이스케이프하지 말고 "
    "그대로 넣는다. questions_file은 JSON 문자열이 아니라 객체로 넘긴다. "
    "multi_select 규칙: 여러 개를 골라도 자연스러운 질문(대상 고객군, 페인포인트 유형 등)은 "
    "true, 배타적 선택(Path/모드 선택 등)은 false(기본). "
    "multi_select 질문의 답변은 'A,C'처럼 콤마로 조인되어 돌아온다. "
    "일반 보기(single-select) 답변은 'B' 또는 'B: 부연설명' 형태로 돌아온다 — "
    "': ' 뒤 부연은 사용자가 그 보기를 고르며 덧붙인 요청/조건이므로 반드시 읽고 반영한다."
)


def _outside_docs(path: str, workspace: str) -> str | None:
    """`aiplc-docs/` 밖 쓰기면 모델에게 돌려줄 거부 문구, 아니면 None.

    왜 여기에도 있는가: 실효 게이트는 ClaudeDriver의 PreToolUse 훅이지만
    (agent/discovery_guard.py 헤더의 2026-08-16 결함 기록), StrandsDriver는
    **롤백 경로**다. 한쪽만 막으면 드라이버를 되돌린 순간 결함이 조용히
    되살아난다 — 질문 답변 되기록을 두 드라이버에 모두 배선한 것과 같은 이유다.
    `_confine`은 워크스페이스 **탈출**만 막으므로 `prototype/index.html`을
    그대로 통과시킨다.

    문구가 한국어 리터럴인 것은 이 파일의 관례다(도구 설명·QUESTIONS_SCHEMA_HINT가
    모두 한국어 고정). build_tools에 language가 없고, 그 3-인자 시그니처는
    driver.py가 기대하는 것이라 바꾸지 않는다 — 언어를 아는 거부 문구는
    agent/prompts.py를 쓰는 ClaudeDriver 쪽에 있다.
    """
    offender = write_denial(path, workspace)
    if offender is None:
        return None
    return (f"거부됨 — Discovery는 'aiplc-docs/' 아래에만 쓸 수 있고 "
            f"'{offender}'는 그 밖이다. 프로토타입을 만들고 실행하는 것은 "
            "Prototypes 탭의 일이다. 대신 "
            "'aiplc-docs/discovery/prototypes/{slug}/PROTOTYPE-{slug}.md'에 "
            "스펙을 쓸 것.")


def _confine(root: str, rel: str) -> Path:
    """rel을 root에 붙여 해석하고 탈출을 거부한다(escape → ValueError)."""
    base = Path(root).resolve()
    p = (base / rel).resolve()
    if not p.is_relative_to(base) or rel.startswith("/"):
        raise ValueError(f"path escapes root: {rel}")
    return p


def build_tools(workspace: str, rules_dir: str,
                emit: Callable[[AgentEvent], None]) -> list:
    """워크스페이스 + 룰 디렉토리 + 이벤트 싱크에 바인딩된 6개 도구.

    file_read는 aiplc-rules/ 프리픽스면 rules_dir(읽기 전용)에서 읽고, 프리픽스는
    rules_dir 루트 기준으로 벗겨서 해석한다(rules_dir 자체가 aiplc-rules 루트이므로
    프리픽스를 그대로 붙이면 이중 중첩된다). 그 외는 workspace로 라우팅한다 —
    구조상 VM 이미지에 구워졌던 /workspace/aiplc-rules를 대체한다. file_write/
    file_append는 항상 workspace만 대상으로 한다(룰은 데이터, 산출물 아님 — 쓰기 금지)."""

    @tool(context=True)
    def ask_questions(questions_file: Any, tool_context: Any) -> str:
        """사용자에게 객관식 질문 세트를 제시하고 답변을 기다린다. 질문은
        반드시 이 도구로만 전달한다(파일로만 남기지 말 것).

        Args:
            questions_file: 질문 파일 페이로드(dict) — name/preamble/questions.
        """
        # 모델 페이로드를 UI 계약으로 정규화한다. 프롬프트 규약만으로는 못 막는
        # 위반이 실재했다(is_other 중복 → "Other — 직접 입력" 두 개가 렌더되고
        # 같은 상태를 공유해 선택이 깨짐). 고칠 수 있는 건 조용히 교정하고,
        # 질문이 성립하지 않으면 이유를 문자열로 돌려준다 — 예외를 그대로
        # 올리면 턴 전체가 죽어 사용자에게 빈 말풍선만 남는다.
        try:
            payload = normalize_questions_payload(questions_file)
        except ValueError as e:
            _log.warning("ask_questions payload rejected: %s", e)
            return (f"질문 폼을 만들 수 없다: {e}\n"
                    f"{QUESTIONS_SCHEMA_HINT}\n"
                    "위 형식에 맞춰 ask_questions를 다시 호출해라.")
        answers = tool_context.interrupt(
            "ask_questions", reason={"questions_payload": payload})
        # 답변을 질문 파일의 `[Answer]:` 칸에도 심는다 — ai-plc 워크플로우가 그
        # 칸을 읽는다(agent/question_file_answers.py 헤더). claude_driver와 같은
        # 되기록을 쓰는 이유는 두 드라이버가 같은 계약을 내야 하기 때문이다:
        # 한쪽만 심으면 드라이버를 롤백했을 때 조용히 빈 칸으로 돌아간다.
        for rel in record_answers(workspace, payload.get("questions") or [],
                                  answers if isinstance(answers, dict) else {}):
            emit(AgentEvent(kind="file_changed", path=rel))
        # 언어 중립 접두사. session_history가 이것을 벗겨 answers dict를
        # 복원한다 — 그쪽은 구 트랜스크립트의 "사용자 답변: "도 함께 받는다.
        return f"{ANSWER_PREFIX}{json.dumps(answers, ensure_ascii=False)}"

    @tool
    def report_stage(stage: str, status: str, summary: str = "") -> str:
        """Discovery 스테이지 전이를 선언한다. aiplc-state.md도 자동 갱신된다.

        Args:
            stage: 스테이지 이름 (예: "Envision").
            status: "pending" | "in_progress" | "completed".
            summary: 한 줄 요약.
        """
        if status not in ("pending", "in_progress", "completed"):
            return f"invalid status '{status}' — use pending|in_progress|completed"
        emit(AgentEvent(kind="stage", payload=json.dumps(
            {"stage": stage, "status": status, "summary": summary}, ensure_ascii=False)))
        # 상태 파일 보장(코드 강제): 대시보드/목록/게이트가 읽는
        # aiplc-docs/aiplc-state.md를 이 시점에 기계적으로 upsert한다.
        # 실패는 이벤트/반환을 막지 않는다(fail-soft) — 화면 이벤트가 우선.
        try:
            p = _confine(workspace, "aiplc-docs/aiplc-state.md")
            existing = p.read_text(encoding="utf-8") if p.is_file() else None
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(upsert_stage(existing, stage, status), encoding="utf-8")
            emit(AgentEvent(kind="file_changed", path="aiplc-docs/aiplc-state.md"))
        except Exception:
            _log.exception("aiplc-state.md upsert failed (stage=%s)", stage)
        return f"stage recorded: {stage} ({status})"

    @tool
    def submit_document(path: str, version: str, summary: str = "") -> str:
        """리뷰 대상 문서가 준비/갱신되었음을 선언한다. **먼저 file_write로 파일을
        쓴 뒤** 호출해야 한다 — 파일이 없거나 비어 있으면 선언이 거부된다.

        Args:
            path: 워크스페이스 상대 경로.
            version: 버전 라벨 (예: "v2").
            summary: 변경 요약.
        """
        # 이 이벤트가 UI의 "문서가 준비됐다"는 유일한 근거다(채팅 카드 + 문서
        # 패널의 activeDoc). 파일 존재를 확인하지 않으면 file_write 없이 이
        # 도구만 호출한 턴이 "생성됐습니다"로 보이고, 정작 문서 패널은 빈
        # 화면을, 새로고침 후에는 목록에서 사라진 문서를 보여준다. 도구가
        # 거짓을 선언할 수 없게 여기서 막는다 — 반환 문자열은 에이전트가 읽고
        # 스스로 고칠 수 있도록 무엇을 해야 하는지 알려준다.
        try:
            p = _confine(workspace, path)
        except ValueError as exc:
            return f"거부됨 — {exc}. 워크스페이스 상대 경로만 제출할 수 있다."
        if not p.is_file():
            return (f"거부됨 — '{path}' 파일이 없다. file_write로 문서를 먼저 "
                    f"저장한 뒤 submit_document를 다시 호출할 것.")
        if not p.read_text(encoding="utf-8", errors="replace").strip():
            return (f"거부됨 — '{path}'가 비어 있다. file_write로 내용을 채운 뒤 "
                    f"submit_document를 다시 호출할 것.")
        emit(AgentEvent(kind="document", payload=json.dumps(
            {"path": path, "version": version, "summary": summary}, ensure_ascii=False)))
        return f"document submitted: {path} {version}"

    @tool
    def file_read(path: str) -> str:
        """워크스페이스 파일 또는 룰(aiplc-rules/ 프리픽스)을 읽는다.

        Args:
            path: 상대 경로. 'aiplc-rules/'로 시작하면 읽기 전용 룰 디렉토리에서,
                  그 외에는 프로젝트 워크스페이스에서 읽는다. aiplc-rules/ 프리픽스는
                  rules_dir 루트 기준으로 벗겨서 해석한다(rules_dir 자체가 aiplc-rules
                  루트이므로 프리픽스를 그대로 붙이면 이중 중첩된다).
        """
        if path.startswith("aiplc-rules/"):
            return _confine(rules_dir, path[len("aiplc-rules/"):]).read_text(encoding="utf-8")
        return _confine(workspace, path).read_text(encoding="utf-8")

    @tool
    def file_write(path: str, content: str) -> str:
        """워크스페이스 파일 전체를 덮어쓴다 — content가 파일의 유일한 내용이 된다.
        기존 내용에 덧붙이려면(특히 audit.md) 반드시 file_append를 사용할 것.

        Args:
            path: 워크스페이스 상대 경로.
            content: 파일 전체 내용.
        """
        refusal = _outside_docs(path, workspace)
        if refusal:
            return refusal
        p = _confine(workspace, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        emit(AgentEvent(kind="file_changed", path=path))
        return f"written: {path}"

    @tool
    def file_append(path: str, content: str) -> str:
        """워크스페이스 파일 끝에 content를 덧붙인다 — 기존 내용은 보존된다.
        audit.md 엔트리 추가 등 누적 기록에 사용. 파일이 없으면 새로 만든다.

        Args:
            path: 워크스페이스 상대 경로.
            content: 덧붙일 내용.
        """
        refusal = _outside_docs(path, workspace)
        if refusal:
            return refusal
        p = _confine(workspace, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(content)
        emit(AgentEvent(kind="file_changed", path=path))
        return f"appended: {path}"

    return [ask_questions, report_stage, submit_document, file_read, file_write, file_append]
