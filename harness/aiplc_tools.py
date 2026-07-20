# harness/aiplc_tools.py — the agent's UI contact points (spec §3).
# Code enforces the UI contract; the rules (markdown) drive the content.
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Callable
from strands import tool
from events import AgentEvent

# Injected into the system prompt so the model produces payloads the frontend
# QuestionForm renders unchanged (mirror of backend models.QuestionFile).
QUESTIONS_SCHEMA_HINT = (
    "ask_questions의 questions_file 인자는 반드시 다음 JSON 형태여야 한다: "
    '{"name": str, "preamble": str|null, "parse_ok": true, "raw_markdown": null, '
    '"questions": [{"number": int, "category": str|null, "text": str, "answer": null, '
    '"multi_select": bool, "options": [{"letter": "A".."F"|"X", "text": str, '
    '"is_other": bool, "recommended": bool}]}]}. '
    "multi_select 규칙: 여러 개를 골라도 자연스러운 질문(대상 고객군, 페인포인트 유형 등)은 "
    "true, 배타적 선택(Path/모드 선택 등)은 false(기본). "
    "multi_select 질문의 답변은 'A,C'처럼 콤마로 조인되어 돌아온다."
)


def _confine(workspace: str, rel: str) -> Path:
    """Resolve rel against the workspace and reject escapes (same guarantee
    as claude_driver._rel, but raising — a tool error is surfaced to the
    model as a tool failure, not silently ignored)."""
    ws = Path(workspace).resolve()
    p = (ws / rel).resolve()
    if not p.is_relative_to(ws) or rel.startswith("/"):
        raise ValueError(f"path escapes workspace: {rel}")
    return p


def build_tools(workspace: str, emit: Callable[[AgentEvent], None]) -> list:
    """Build the five tools bound to this workspace + event sink. `emit` is
    called synchronously during tool execution; the driver drains it into
    the SSE stream."""

    @tool(context=True)
    def ask_questions(questions_file: dict, tool_context: Any) -> str:
        """사용자에게 객관식 질문 세트를 제시하고 답변을 기다린다. 질문은
        반드시 이 도구로만 전달한다(파일로만 남기지 말 것). questions_file은
        QUESTIONS_SCHEMA_HINT의 JSON 스키마를 따라야 한다.

        Args:
            questions_file: 질문 파일 페이로드(dict) — name/preamble/questions.
        """
        # NOTE: interrupt() 앞에 부작용 금지 — resume 시 이 함수는 처음부터
        # 재실행되고 interrupt()가 사용자 답변을 반환한다(재실행 모델).
        answers = tool_context.interrupt(
            "ask_questions", reason={"questions_payload": questions_file})
        return f"사용자 답변: {json.dumps(answers, ensure_ascii=False)}"

    @tool
    def report_stage(stage: str, status: str, summary: str = "") -> str:
        """Discovery 스테이지 전이를 선언한다. 스테이지를 시작/완료할 때마다
        반드시 호출한다(aiplc-state.md 기록과 별개).

        Args:
            stage: 스테이지 이름 (예: "Envision").
            status: "pending" | "in_progress" | "completed".
            summary: 한 줄 요약.
        """
        if status not in ("pending", "in_progress", "completed"):
            return f"invalid status '{status}' — use pending|in_progress|completed"
        emit(AgentEvent(kind="stage", payload=json.dumps(
            {"stage": stage, "status": status, "summary": summary}, ensure_ascii=False)))
        return f"stage recorded: {stage} ({status})"

    @tool
    def submit_document(path: str, version: str, summary: str = "") -> str:
        """discovery-document 등 리뷰 대상 문서가 준비/갱신되었음을 선언한다.

        Args:
            path: 워크스페이스 상대 경로.
            version: 버전 라벨 (예: "v2").
            summary: 변경 요약.
        """
        emit(AgentEvent(kind="document", payload=json.dumps(
            {"path": path, "version": version, "summary": summary}, ensure_ascii=False)))
        return f"document submitted: {path} {version}"

    @tool
    def file_read(path: str) -> str:
        """워크스페이스 파일을 읽는다 (룰 상세 로드 등).

        Args:
            path: 워크스페이스 상대 경로.
        """
        return _confine(workspace, path).read_text(encoding="utf-8")

    @tool
    def file_write(path: str, content: str) -> str:
        """워크스페이스 파일 전체를 덮어쓴다 — content가 파일의 유일한 내용이 된다.
        기존 내용에 덧붙이려면(특히 audit.md 엔트리 추가) 반드시 file_append를
        사용할 것. 부분 내용으로 file_write를 호출하면 이전 내용이 전부 유실된다.

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
