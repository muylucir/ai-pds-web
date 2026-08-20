# backend/tests/test_proto_tools.py — 프로토타입 빌더의 커스텀 MCP 도구.
from __future__ import annotations

import json

from aipds.models import AgentEvent
from aipds.proto.tools import (BUILD_COMPLETE_TOOL, PROTO_MCP_SERVER_NAME,
                                    build_proto_tools)


def _handler(workspace, emit):
    """build_proto_tools가 돌려주는 @tool dataclass에서 핸들러를 꺼낸다.
    claude_agent_sdk의 @tool은 SdkMcpTool(name=..., handler=...)를 만든다."""
    tools = build_proto_tools(str(workspace), emit)
    by_name = {t.name: t.handler for t in tools}
    return by_name["build_complete"]


def _prototype_dir(tmp_path):
    d = tmp_path / "prototype"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_tool_name_constant_matches_the_sdk_spelling():
    """allowed_tools의 항목은 mcp__<서버 키>__<도구 이름>이어야 한다 —
    다른 표기는 조용히 승인 대기로 남는다(claude_driver.py:419-422)."""
    assert BUILD_COMPLETE_TOOL == f"mcp__{PROTO_MCP_SERVER_NAME}__build_complete"


async def test_complete_emits_a_build_complete_event(tmp_path):
    _prototype_dir(tmp_path).joinpath("index.html").write_text("<h1>hi</h1>")
    seen: list[AgentEvent] = []
    handler = _handler(tmp_path, seen.append)

    await handler({"summary": "할 일 앱을 만들었다", "remaining": "다크 모드"})

    assert len(seen) == 1
    ev = seen[0]
    assert ev.kind == "build_complete"
    payload = json.loads(ev.payload)
    assert payload == {"summary": "할 일 앱을 만들었다", "remaining": "다크 모드"}


async def test_remaining_is_optional(tmp_path):
    _prototype_dir(tmp_path).joinpath("index.html").write_text("x")
    seen: list[AgentEvent] = []
    handler = _handler(tmp_path, seen.append)

    await handler({"summary": "완성"})

    assert json.loads(seen[0].payload)["remaining"] == ""


async def test_completion_is_refused_when_prototype_dir_is_missing(tmp_path):
    """도구가 거짓을 선언할 수 없게 막는다 — submit_document와 같은 규율
    (agent/tools.py). 반환 문자열은 에이전트가 읽고 스스로 고칠 수 있어야 한다."""
    seen: list[AgentEvent] = []
    handler = _handler(tmp_path, seen.append)   # prototype/ 없음

    result = await handler({"summary": "다 했다"})

    assert seen == []                            # 이벤트가 나가지 않는다
    text = result["content"][0]["text"]
    assert "prototype/" in text


async def test_completion_is_refused_when_prototype_dir_is_empty(tmp_path):
    _prototype_dir(tmp_path)                     # 만들지만 비어 있다
    seen: list[AgentEvent] = []
    handler = _handler(tmp_path, seen.append)

    result = await handler({"summary": "다 했다"})

    assert seen == []
    assert "prototype/" in result["content"][0]["text"]


async def test_a_successful_completion_returns_text_for_the_agent(tmp_path):
    _prototype_dir(tmp_path).joinpath("index.html").write_text("x")
    handler = _handler(tmp_path, lambda ev: None)

    result = await handler({"summary": "완성"})

    assert result["content"][0]["type"] == "text"


async def test_build_complete_is_refused_without_the_brand_theme(tmp_path):
    from aipds.design_profile import DesignProfile
    from aipds.proto.design_sync import sync_design

    (tmp_path / "prototype").mkdir()
    (tmp_path / "prototype" / "package.json").write_text("{}", encoding="utf-8")
    sync_design(tmp_path, DesignProfile(
        filename="a.md", uploaded_at="t", uploaded_by="x", markdown="(원문)",
        tokens={"primary": "#5b2ea6"}, prose=""), "ko")

    seen: list[AgentEvent] = []
    handler = _handler(tmp_path, seen.append)

    result = await handler({"summary": "다 만들었다"})

    assert "aipds-theme.css" in result["content"][0]["text"]
    # 거부는 세션을 끝내지 않는다 — 이벤트가 나가지 않아야 한다.
    assert seen == []


async def test_build_complete_passes_once_the_theme_is_imported(tmp_path):
    from aipds.design_profile import DesignProfile
    from aipds.proto.design_sync import THEME_FILENAME, sync_design

    app = tmp_path / "prototype" / "app"
    app.mkdir(parents=True)
    sync_design(tmp_path, DesignProfile(
        filename="a.md", uploaded_at="t", uploaded_by="x", markdown="(원문)",
        tokens={"primary": "#5b2ea6"}, prose=""), "ko")
    (app / THEME_FILENAME).write_text(
        (tmp_path / THEME_FILENAME).read_text(), encoding="utf-8")
    (app / "globals.css").write_text('@import "./aipds-theme.css";',
                                     encoding="utf-8")

    seen: list[AgentEvent] = []
    handler = _handler(tmp_path, seen.append)

    await handler({"summary": "다 만들었다"})

    assert [e.kind for e in seen] == ["build_complete"]


async def test_build_complete_skips_the_theme_check_when_the_profile_has_no_tokens(tmp_path):
    """0토큰 프로필은 테마 import를 강제하지 않는다 — 강제할 값이 없다.

    이것은 **의도된 한계**다: 그 경우 브랜드는 DESIGN.md 산문을 통해서만 가고
    (design_rules(has_tokens=False)가 globals.css로 옮기라고 지시한다), 우리는
    "옮겼는지"를 값싸게 검사할 방법이 없다. 검사할 수 없는 것을 통과시키는 대신
    거짓으로 통과시키지는 않는다 — 루트 테마 파일이 no-profile 스텁이므로
    "브랜드 적용됨"이라고 주장하는 파일이 워크스페이스에 남지 않는다.
    """
    from aipds.design_profile import DesignProfile
    from aipds.proto.design_sync import sync_design

    (tmp_path / "prototype").mkdir()
    (tmp_path / "prototype" / "package.json").write_text("{}", encoding="utf-8")
    sync_design(tmp_path, DesignProfile(
        filename="a.md", uploaded_at="t", uploaded_by="x", markdown="(원문)",
        tokens={}, prose="## 톤\n여백을 넉넉히."), "ko")

    seen: list[AgentEvent] = []
    handler = _handler(tmp_path, seen.append)

    await handler({"summary": "다 만들었다"})

    assert [e.kind for e in seen] == ["build_complete"]


async def test_build_complete_skips_the_theme_check_without_a_profile(tmp_path):
    (tmp_path / "prototype").mkdir()
    (tmp_path / "prototype" / "package.json").write_text("{}", encoding="utf-8")

    seen: list[AgentEvent] = []
    handler = _handler(tmp_path, seen.append)

    await handler({"summary": "다 만들었다"})

    assert [e.kind for e in seen] == ["build_complete"]
