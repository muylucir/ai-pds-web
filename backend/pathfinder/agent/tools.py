# backend/pathfinder/agent/tools.py — 에이전트의 UI 접점.
#
# 커스텀 도구는 둘뿐이다. 파일 조작(Read/Write/Edit)과 질문(AskUserQuestion)은
# Claude Agent SDK 내장 도구가 담당한다 — AI-PLC 룰이 전제한 그 도구들이며,
# 자작했던 file_read의 `aiplc-rules/` 프리픽스 특수 처리는 룰을 워크스페이스에
# 배치(agent/workspace_rules.py)하면서 필요 없어졌다.
#
# 여기 남는 둘은 상류 룰에 없는 우리 UI 요구다: 스테이지 사이드바와 문서 패널은
# 모델의 명시적 선언이 있어야 신뢰할 수 있다. aiplc-state.md 쓰기에서
# 역추론하면 한 턴에 여러 번 갱신될 때 UI가 흔들린다.
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Any, Callable
from claude_agent_sdk import tool
from pathfinder.models import AgentEvent
from pathfinder.agent.state_sync import upsert_stage

_log = logging.getLogger("pathfinder.agent")


def _confine(root: str, rel: str) -> Path:
    """rel을 root에 붙여 해석하고 탈출을 거부한다(escape → ValueError)."""
    base = Path(root).resolve()
    p = (base / rel).resolve()
    if not p.is_relative_to(base) or rel.startswith("/"):
        raise ValueError(f"path escapes root: {rel}")
    return p


# 명시적 JSON Schema 딕셔너리를 쓴다(@tool의 dict-형태 숏컷 {"key": type, ...}
# 대신). 그 숏컷은 모든 키를 required로 만든다(claude_agent_sdk/__init__.py의
# create_sdk_mcp_server._build_schema: `"required": list(properties.keys())`) —
# summary는 원래 도구들처럼(`summary: str = ""`) 생략 가능해야 하므로 맞지
# 않는다. TypedDict + NotRequired도 시도했으나 이 파일의
# `from __future__ import annotations`(PEP 563, 문자열화된 애노테이션) 때문에
# 런타임에 stdlib typing이 NotRequired를 인식하지 못해 required로 강등되는
# 것을 실측으로 확인했다(get_type_hints로 재평가해도 TypedDict 메타클래스가
# 클래스 본문 평가 시점에 이미 잘못 분류함) — 그래서 하드코딩된 스키마가 가장
# 안전하다.
_REPORT_STAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "stage": {"type": "string"},
        "status": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["stage", "status"],
}

_SUBMIT_DOCUMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "version": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["path", "version"],
}


def _text_result(text: str) -> dict[str, Any]:
    """claude_agent_sdk의 @tool 핸들러 반환 계약: {"content": [{"type":"text",
    "text": ...}]} — create_sdk_mcp_server.call_tool이 이 형태를 CallToolResult로
    변환한다(claude_agent_sdk/__init__.py:456-522)."""
    return {"content": [{"type": "text", "text": text}]}


def build_tools(workspace: str, emit: Callable[[AgentEvent], None]) -> list:
    """워크스페이스 + 이벤트 싱크에 바인딩된 2개의 SdkMcpTool(claude_agent_sdk의
    @tool 데코레이터가 만드는 dataclass)을 리스트로 돌려준다.

    이 리스트 자체는 ClaudeAgentOptions에 바로 넣을 수 없다 — 호출부(Task 6의
    claude_driver.py)가 create_sdk_mcp_server(name=..., tools=build_tools(...))로
    감싸 McpSdkServerConfig를 만들고, 그것을 ClaudeAgentOptions에 배선한다:

        server = create_sdk_mcp_server(name="pathfinder", tools=build_tools(workspace, emit))
        options = ClaudeAgentOptions(
            mcp_servers={"pathfinder": server},
            allowed_tools=["mcp__pathfinder__report_stage",
                           "mcp__pathfinder__submit_document"],
            ...
        )

    allowed_tools의 이름은 SDK가 강제하는 `mcp__<server-key>__<tool-name>`
    형식이다(claude_agent_sdk 배포 문서의 calculator 예제와 동일 패턴 —
    mcp_servers={"tools": server} + allowed_tools=["mcp__tools__greet"]). 이
    프리픽스가 없으면 권한 승인 프롬프트로 떨어진다 — bypassPermissions에서는
    허용되지만 다른 permission_mode에서는 매 호출마다 멈춘다.
    """

    @tool("report_stage", "Discovery 스테이지 전이를 선언한다. aiplc-state.md도 자동 갱신된다.",
         _REPORT_STAGE_SCHEMA)
    async def report_stage(args: dict[str, Any]) -> dict[str, Any]:
        stage = args["stage"]
        status = args["status"]
        summary = args.get("summary", "")
        if status not in ("pending", "in_progress", "completed"):
            return _text_result(
                f"invalid status '{status}' — use pending|in_progress|completed")
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
        return _text_result(f"stage recorded: {stage} ({status})")

    @tool("submit_document",
         "리뷰 대상 문서가 준비/갱신되었음을 선언한다. **먼저 Write/Edit로 파일을 "
         "쓴 뒤** 호출해야 한다 — 파일이 없거나 비어 있으면 선언이 거부된다.",
         _SUBMIT_DOCUMENT_SCHEMA)
    async def submit_document(args: dict[str, Any]) -> dict[str, Any]:
        path = args["path"]
        version = args["version"]
        summary = args.get("summary", "")
        # 이 이벤트가 UI의 "문서가 준비됐다"는 유일한 근거다(채팅 카드 + 문서
        # 패널의 activeDoc). 파일 존재를 확인하지 않으면 Write 없이 이 도구만
        # 호출한 턴이 "생성됐습니다"로 보이고, 정작 문서 패널은 빈 화면을,
        # 새로고침 후에는 목록에서 사라진 문서를 보여준다. 도구가 거짓을
        # 선언할 수 없게 여기서 막는다 — 반환 문자열은 에이전트가 읽고 스스로
        # 고칠 수 있도록 무엇을 해야 하는지 알려준다.
        try:
            p = _confine(workspace, path)
        except ValueError as exc:
            return _text_result(f"거부됨 — {exc}. 워크스페이스 상대 경로만 제출할 수 있다.")
        if not p.is_file():
            return _text_result(
                f"거부됨 — '{path}' 파일이 없다. Write로 문서를 먼저 "
                f"저장한 뒤 submit_document를 다시 호출할 것.")
        if not p.read_text(encoding="utf-8", errors="replace").strip():
            return _text_result(
                f"거부됨 — '{path}'가 비어 있다. Write로 내용을 채운 뒤 "
                f"submit_document를 다시 호출할 것.")
        emit(AgentEvent(kind="document", payload=json.dumps(
            {"path": path, "version": version, "summary": summary}, ensure_ascii=False)))
        return _text_result(f"document submitted: {path} {version}")

    return [report_stage, submit_document]
