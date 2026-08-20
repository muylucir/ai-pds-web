# backend/aipds/agent/tools.py — 에이전트의 UI 접점.
#
# **커스텀 도구는 하나뿐이다: `submit_document`.** 파일 조작(Read/Write/Edit)과
# 질문(AskUserQuestion)은 Claude Agent SDK 내장 도구가 담당한다 — AI-PLC 룰이
# 전제한 그 도구들이며, 자작했던 file_read의 `aiplc-rules/` 프리픽스 특수 처리는
# 룰을 워크스페이스에 배치(agent/workspace_rules.py)하면서 필요 없어졌다.
#
# **한때 셋이었다.** `report_stage`와 `handoff_prototype`은 2026-08-18에 훅으로
# 옮겨 갔다(agent/reconcile.py 헤더에 전말이 있다). 이 파일의 옛 헤더는 그 둘의
# 근거를 "스테이지 사이드바와 문서 패널은 모델의 명시적 선언이 있어야 신뢰할 수
# 있다"고 적었는데, 그 문장은 반쪽이었다: 선언은 신뢰할 수 있지만 **선언이
# 일어난다는 보장이 없다.** 도구는 모델이 부르지 않으면 침묵하고, 그 침묵이 두 번
# 실측됐다(배지가 프로젝트 내내 빈 test123456, 탭 안내 0회의 keumkang-v5).
#
# **그럼 왜 이 하나는 남는가.** 판정 기준은 "신호가 파일에서 파싱되는가"다.
# 스테이지는 `aiplc-state.md`에서 파싱되고 인계는 `build-instructions.md`의
# 존재에서 파싱되지만, `submit_document`의 `version`은 파일에 없고 "리뷰
# 준비됨 vs 중간 저장"도 파싱이 아니라 판단이다. 문서 패널 자체는 이미
# 결정론적이다 — 프론트가 `file_changed`로 활성 문서를 잡는다
# (useWorkspaceStream.ts의 `isDocPath`). 이 도구가 더하는 것은 채팅 카드와
# 버전이고, 둘 다 모델만 아는 것이다.
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Any, Callable
from claude_agent_sdk import tool
from aipds.models import AgentEvent
from aipds.agent import prompts

_log = logging.getLogger("aipds.agent")


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


def build_tools(workspace: str, emit: Callable[[AgentEvent], None],
                language: str = "ko") -> list:
    """워크스페이스 + 이벤트 싱크에 바인딩된 SdkMcpTool(claude_agent_sdk의 @tool
    데코레이터가 만드는 dataclass) 리스트. 지금은 `submit_document` 하나다.

    **`publish` 인자가 없어졌다.** 그것은 `report_stage`만의 요구였다: 그 도구가
    `aiplc-state.md`를 로컬에 직접 쓰면서 PostToolUse 훅을 지나지 않았고, 그래서
    훅이 지키는 계약("광고하기 전에 게시한다", aipds/workspace_sync.py)을
    스스로 다시 지켜야 했다. 스테이지가 훅으로 옮겨 간 지금은 상태 파일도 다른
    산출물과 **같은 경로**로 게시된다 — 예외가 사라졌으므로 예외를 위한 인자도
    사라진다. `submit_document`는 파일을 쓰지 않고 이미 쓰인 파일을 선언할 뿐이라
    게시할 것이 없다.

    language는 **도구 설명과 반환 문자열**의 언어다 — 둘 다 모델이 읽는
    프롬프트이므로 대화 언어와 맞아야 한다(proto/tools.py가 같은 계약이다).
    이 인자가 없던 동안 영어 프로젝트의 에이전트도 매 턴 한국어 도구 설명을
    읽었고, 그것이 2026-08-04에 영어 프로젝트가 한국어로 대화한 원인의 일부였다
    (agent/prompts.py 헤더에 전말이 있다).

    이 리스트 자체는 ClaudeAgentOptions에 바로 넣을 수 없다 — 호출부
    (claude_driver.py)가 create_sdk_mcp_server(name=..., tools=build_tools(...))로
    감싸 McpSdkServerConfig를 만들고, 그것을 ClaudeAgentOptions에 배선한다:

        server = create_sdk_mcp_server(name="pathfinder", tools=build_tools(workspace, emit))
        options = ClaudeAgentOptions(
            mcp_servers={"pathfinder": server},
            allowed_tools=["mcp__pathfinder__submit_document"],
            ...
        )

    allowed_tools의 이름은 SDK가 강제하는 `mcp__<server-key>__<tool-name>`
    형식이다(claude_agent_sdk 배포 문서의 calculator 예제와 동일 패턴 —
    mcp_servers={"tools": server} + allowed_tools=["mcp__tools__greet"]). 이
    프리픽스가 없으면 권한 승인 프롬프트로 떨어진다 — bypassPermissions에서는
    허용되지만 다른 permission_mode에서는 매 호출마다 멈춘다.
    """

    @tool("submit_document", prompts.submit_document_description(language),
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
            return _text_result(prompts.submit_document_escape(language, str(exc)))
        if not p.is_file():
            return _text_result(prompts.submit_document_missing(language, path))
        if not p.read_text(encoding="utf-8", errors="replace").strip():
            return _text_result(prompts.submit_document_empty(language, path))
        emit(AgentEvent(kind="document", payload=json.dumps(
            {"path": path, "version": version, "summary": summary}, ensure_ascii=False)))
        return _text_result(f"document submitted: {path} {version}")

    return [submit_document]
