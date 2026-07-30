# backend/pathfinder/proto/tools.py — 프로토타입 빌더의 커스텀 MCP 도구.
#
# 하나뿐이다: build_complete. 파일 조작과 질문은 SDK 내장 도구가 담당한다
# (Write/Edit/AskUserQuestion). 이것만 자작하는 이유는 Discovery의
# report_stage와 같다 — "빌드가 끝났다"는 사실은 모델의 명시적 선언이 있어야
# 신뢰할 수 있다. 산출물 존재나 done 이벤트에서 역추론하면 빌드 중간 턴을
# 완료로 오판한다(done은 "이 턴이 끝났다"는 뜻일 뿐이다).
#
# 이 선언이 세션의 수명을 끝낸다: proto/session.py가 이 이벤트를 관찰해
# status를 "complete"로 바꾸고 handoff.json을 쓴 뒤 유휴 타이머로 세션을
# 닫는다. 그래서 도구가 거짓을 선언할 수 없어야 하고, 아래 산출물 검증이
# 그것을 막는다.
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from claude_agent_sdk import tool

from pathfinder.models import AgentEvent

_log = logging.getLogger("pathfinder.proto")

#: Discovery의 "pathfinder"와 구분되는 값 — 두 드라이버는 서로 다른 도구
#: 집합을 노출한다. 같은 이름을 쓰면 어느 쪽 도구가 붙었는지 로그에서
#: 구분되지 않는다.
PROTO_MCP_SERVER_NAME = "pathfinder_proto"

#: allowed_tools에 넣을 정규 이름. SDK가 --mcp-config를 직렬화할 때 이
#: 형태로 이름을 만들므로, 다른 표기는 조용히 승인 대기로 남는다
#: (agent/claude_driver.py:419-422의 같은 지적).
BUILD_COMPLETE_TOOL = f"mcp__{PROTO_MCP_SERVER_NAME}__build_complete"

# 명시적 JSON Schema를 쓴다. @tool의 dict 숏컷({"key": type})은 모든 키를
# required로 만들어(create_sdk_mcp_server._build_schema) remaining을 생략할
# 수 없게 된다 — agent/tools.py:32-41이 같은 이유로 같은 선택을 했다.
_BUILD_COMPLETE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "remaining": {"type": "string"},
    },
    "required": ["summary"],
}


def _text_result(text: str) -> dict[str, Any]:
    """@tool 핸들러의 반환 계약 — create_sdk_mcp_server.call_tool이 이 형태를
    CallToolResult로 변환한다."""
    return {"content": [{"type": "text", "text": text}]}


def _has_output(workspace: str) -> bool:
    """prototype/ 아래에 무엇이든 있는가.

    _local_build_exists(routes/prototypes.py:155-170)와 같은 기준을 쓴다:
    직속 자식 하나라도 있으면 참, 재귀 스캔은 하지 않는다(node_modules가
    생긴 뒤에도 싸게 유지된다). 두 곳이 다른 기준을 쓰면 도구는 완료를
    받아들이는데 목록은 built로 보이지 않는(또는 그 반대) 상태가 된다.
    """
    proto_dir = Path(workspace) / "prototype"
    try:
        return proto_dir.is_dir() and any(proto_dir.iterdir())
    except OSError:
        return False


def build_proto_tools(workspace: str,
                      emit: Callable[[AgentEvent], None]) -> list:
    """워크스페이스 + 이벤트 싱크에 바인딩된 SdkMcpTool 리스트.

    Discovery의 build_tools와 같은 계약이다 — 이 리스트 자체는
    ClaudeAgentOptions에 바로 넣을 수 없고, 호출부(proto/builder.py)가
    create_sdk_mcp_server(name=PROTO_MCP_SERVER_NAME, tools=...)로 감싼다.
    """

    @tool("build_complete",
          "프로토타입 빌드가 완료되었음을 선언한다. **prototype/ 아래에 실제 "
          "산출물을 만든 뒤** 호출해야 한다 — 비어 있으면 선언이 거부된다. "
          "이 선언 뒤 빌드 세션이 종료되므로, 아직 작업이 남았으면 호출하지 마라.",
          _BUILD_COMPLETE_SCHEMA)
    async def build_complete(args: dict[str, Any]) -> dict[str, Any]:
        summary = args["summary"]
        remaining = args.get("remaining", "")

        # 이 이벤트가 세션을 끝낸다. 산출물 없이 선언되면 사용자는 "빌드
        # 완료" 카드를 보는데 호스팅할 것이 없다 — submit_document가 파일
        # 존재를 확인하는 것과 같은 이유로 여기서 막는다. 반환 문자열은
        # 에이전트가 읽고 스스로 고칠 수 있도록 무엇을 해야 하는지 알려준다.
        if not _has_output(workspace):
            _log.warning("build_complete refused: prototype/ is empty (%s)",
                         workspace)
            return _text_result(
                "거부됨 — 작업 디렉토리의 `prototype/` 아래에 산출물이 없다. "
                "완성물을 `prototype/`에 쓴 뒤 다시 선언해라.")

        emit(AgentEvent(kind="build_complete", payload=json.dumps(
            {"summary": summary, "remaining": remaining}, ensure_ascii=False)))
        return _text_result("빌드 완료가 기록되었다. 세션을 종료한다.")

    return [build_complete]
