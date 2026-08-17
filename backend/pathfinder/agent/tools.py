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
from typing import Any, Awaitable, Callable
from claude_agent_sdk import tool
from pathfinder.models import AgentEvent
from pathfinder.agent import prompts
from pathfinder.agent.state_sync import upsert_stage
from pathfinder.proto import layout

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


#: 넘길 프로토타입의 id 하나. 경로는 우리가 정한다 — 에이전트가 경로를 넘기면
#: 레이아웃 규약이 프롬프트로 새어나가고(proto/layout.py가 단독 소유해야 한다)
#: 틀린 경로를 선언할 여지가 생긴다.
_HANDOFF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"slug": {"type": "string"}},
    "required": ["slug"],
}


def _text_result(text: str) -> dict[str, Any]:
    """claude_agent_sdk의 @tool 핸들러 반환 계약: {"content": [{"type":"text",
    "text": ...}]} — create_sdk_mcp_server.call_tool이 이 형태를 CallToolResult로
    변환한다(claude_agent_sdk/__init__.py:456-522)."""
    return {"content": [{"type": "text", "text": text}]}


def build_tools(workspace: str, emit: Callable[[AgentEvent], None],
                language: str = "ko", *,
                publish: Callable[[str], Awaitable[None]]) -> list:
    """워크스페이스 + 이벤트 싱크에 바인딩된 2개의 SdkMcpTool(claude_agent_sdk의
    @tool 데코레이터가 만드는 dataclass)을 리스트로 돌려준다.

    `publish(rel)`은 워크스페이스 파일 하나를 정본(S3)에 올린다. **키워드 필수**다 —
    기본값을 no-op으로 두면 새 호출부가 조용히 빠뜨리고, 그 실패는 "화면이 낡아
    보인다"로만 나타난다.

    왜 필요한가(2026-08-18 실측): 이 모듈의 `report_stage`는 `aiplc-state.md`를
    **로컬에 직접 쓰고** `emit`으로 알린다. 그래서 claude_driver의 PostToolUse 훅을
    지나지 않고, 그 훅이 지키는 계약("광고하기 전에 게시한다",
    pathfinder/workspace_sync.py)을 빠뜨렸다. UI의 읽기 경로는 전부 정본이므로
    진행률 사이드바가 턴 종료까지 낡은 상태를 읽었다.

    language는 **도구 설명과 반환 문자열**의 언어다 — 둘 다 모델이 읽는
    프롬프트이므로 대화 언어와 맞아야 한다(proto/tools.py가 같은 계약이다).
    이 인자가 없던 동안 영어 프로젝트의 에이전트도 매 턴 한국어 도구 설명을
    읽었고, 그것이 2026-08-04에 영어 프로젝트가 한국어로 대화한 원인의 일부였다
    (agent/prompts.py 헤더에 전말이 있다).

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

    @tool("report_stage", prompts.report_stage_description(language),
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
            # 게시는 upsert **뒤**다 — 앞이면 갱신 전 내용이 정본에 간다. 그리고
            # emit **앞**이다: 이벤트를 받은 UI가 곧바로 읽으러 오는데 그 시점에
            # 정본에 없으면 낡은 상태(또는 404)를 본다.
            await publish("aiplc-docs/aiplc-state.md")
            emit(AgentEvent(kind="file_changed", path="aiplc-docs/aiplc-state.md"))
        except Exception:
            _log.exception("aiplc-state.md upsert/publish failed (stage=%s)", stage)
        return _text_result(f"stage recorded: {stage} ({status})")

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

    @tool("handoff_prototype", prompts.handoff_prototype_description(language),
         _HANDOFF_SCHEMA)
    async def handoff_prototype(args: dict[str, Any]) -> dict[str, Any]:
        """빌드를 Prototypes 탭으로 넘긴다.

        **왜 도구인가(2026-08-17의 결함).** Path A.1의 Step 3은 "Build Prototype"
        이고 상류 Step 4~11은 돌아가는 프로토타입을 전제한다. Pathfinder는 빌드를
        Prototypes 탭이 하므로 그 자리에서 흐름이 끊겼다 — 그런데 금지만 있고
        **대체 행동이 없어서** 에이전트가 즉흥 대응했다(실측 keumkang-v5:
        자격증명 점검 → API 키 요구 → 선행 조건 나열, 탭 안내는 0회).
        report_stage·submit_document와 같은 규율이다: 도구가 행동을 만든다.

        명세 파일 존재를 확인하는 이유는 submit_document와 같다 — 카드는 그 파일에서
        파생되므로(routes/prototypes.py의 discover), 없는데 넘겼다고 하면 사용자가
        빈 탭을 본다.
        """
        slug = args["slug"]
        try:
            rel = layout.spec_key(slug)
            p = _confine(workspace, rel)
        except ValueError as exc:
            return _text_result(prompts.submit_document_escape(language, str(exc)))
        if not p.is_file():
            return _text_result(prompts.handoff_prototype_missing(language, rel))
        # 이 이벤트가 화면의 "Prototypes 탭으로 가기" 카드를 만든다. 에이전트가
        # 안내 문장을 잊어도 사용자에게 클릭할 곳이 남아야 한다 — 지금까지는
        # 안내가 없으면 사용자가 막혔다.
        emit(AgentEvent(kind="prototype_ready", payload=json.dumps(
            {"slug": slug, "spec_path": rel}, ensure_ascii=False)))
        return _text_result(prompts.handoff_prototype_done(language, slug))

    return [report_stage, submit_document, handoff_prototype]
