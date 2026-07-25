# backend/pathfinder/agent/tools.py — 에이전트의 UI 접점(구 harness/aiplc_tools.py).
# 코드가 UI 계약을 강제하고, 룰(markdown)이 내용을 채운다.
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Any, Callable
from strands import tool
from pathfinder.models import AgentEvent
from pathfinder.agent.state_sync import upsert_stage

_log = logging.getLogger("pathfinder.agent")

QUESTIONS_SCHEMA_HINT = (
    "ask_questions의 questions_file 인자는 반드시 다음 JSON 형태여야 한다: "
    '{"name": str, "preamble": str|null, "parse_ok": true, "raw_markdown": null, '
    '"questions": [{"number": int, "category": str|null, "text": str, "answer": null, '
    '"multi_select": bool, "options": [{"letter": "A".."F"|"X", "text": str, '
    '"is_other": bool, "recommended": bool}]}]}. '
    "multi_select 규칙: 여러 개를 골라도 자연스러운 질문(대상 고객군, 페인포인트 유형 등)은 "
    "true, 배타적 선택(Path/모드 선택 등)은 false(기본). "
    "multi_select 질문의 답변은 'A,C'처럼 콤마로 조인되어 돌아온다. "
    "일반 보기(single-select) 답변은 'B' 또는 'B: 부연설명' 형태로 돌아온다 — "
    "': ' 뒤 부연은 사용자가 그 보기를 고르며 덧붙인 요청/조건이므로 반드시 읽고 반영한다."
)


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
    def ask_questions(questions_file: dict, tool_context: Any) -> str:
        """사용자에게 객관식 질문 세트를 제시하고 답변을 기다린다. 질문은
        반드시 이 도구로만 전달한다(파일로만 남기지 말 것).

        Args:
            questions_file: 질문 파일 페이로드(dict) — name/preamble/questions.
        """
        answers = tool_context.interrupt(
            "ask_questions", reason={"questions_payload": questions_file})
        return f"사용자 답변: {json.dumps(answers, ensure_ascii=False)}"

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
        p = _confine(workspace, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(content)
        emit(AgentEvent(kind="file_changed", path=path))
        return f"appended: {path}"

    return [ask_questions, report_stage, submit_document, file_read, file_write, file_append]
