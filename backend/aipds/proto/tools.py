# backend/aipds/proto/tools.py — 프로토타입 빌더의 커스텀 MCP 도구.
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

from aipds.models import AgentEvent
from aipds.proto import prompts
from aipds.proto.design_sync import theme_imported, theme_required
from aipds.proto.session import has_build_output

_log = logging.getLogger("aipds.proto")

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

    판정은 `proto/session.py`의 `has_build_output`에 있다 — "빌드됐다"의 단일
    정의다. 여기, 목록 라우트, 그리고 개시 프롬프트가 같은 질문을 하고, 기준이
    갈라지면 도구는 완료를 받아들이는데 목록은 built로 보이지 않는(또는 그
    반대) 상태가 된다. 이 래퍼는 입력 모양만 맞춘다(workspace 문자열).
    """
    return has_build_output(Path(workspace))


def build_proto_tools(workspace: str,
                      emit: Callable[[AgentEvent], None],
                      language: str = "ko") -> list:
    """워크스페이스 + 이벤트 싱크에 바인딩된 SdkMcpTool 리스트.

    Discovery의 build_tools와 같은 계약이다 — 이 리스트 자체는
    ClaudeAgentOptions에 바로 넣을 수 없고, 호출부(proto/builder.py)가
    create_sdk_mcp_server(name=PROTO_MCP_SERVER_NAME, tools=...)로 감싼다.

    language는 도구 설명과 반환 문자열의 언어다 — 셋 다 모델이 읽는
    프롬프트이므로 대화 언어와 맞아야 한다(proto/prompts.py).
    """

    @tool("build_complete",
          prompts.build_complete_description(language),
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
            return _text_result(prompts.build_complete_rejection(language))

        # 브랜드 프로필이 적용된 워크스페이스인데 테마가 붙지 않았으면 되돌려
        # 보낸다. 판정은 디스크만 본다(design_sync.theme_required) — 도구 호출
        # 경로에서 S3를 타지 않는다. 프로필이 없으면 이 검사는 아예 돌지 않아
        # 기존 동작과 구분되지 않는다.
        build_dir = Path(workspace)
        if theme_required(build_dir) and not theme_imported(build_dir):
            _log.warning("build_complete refused: brand theme not applied (%s)",
                         workspace)
            return _text_result(prompts.build_complete_theme_rejection(language))

        emit(AgentEvent(kind="build_complete", payload=json.dumps(
            {"summary": summary, "remaining": remaining}, ensure_ascii=False)))
        return _text_result(prompts.build_complete_recorded(language))

    return [build_complete]
