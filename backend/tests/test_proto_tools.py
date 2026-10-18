# backend/tests/test_proto_tools.py — the prototype builder's custom MCP tools.
from __future__ import annotations

import json

from aipds.models import AgentEvent
from aipds.proto.tools import (BUILD_COMPLETE_TOOL, PROTO_MCP_SERVER_NAME,
                                    build_proto_tools)


def _handler(workspace, emit):
    """Extracts the handler from the @tool dataclass returned by build_proto_tools.
    claude_agent_sdk's @tool creates SdkMcpTool(name=..., handler=...)."""
    tools = build_proto_tools(str(workspace), emit)
    by_name = {t.name: t.handler for t in tools}
    return by_name["build_complete"]


def _prototype_dir(tmp_path):
    d = tmp_path / "prototype"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_tool_name_constant_matches_the_sdk_spelling():
    """Entries in allowed_tools must be mcp__<server key>__<tool name> —
    any other notation silently stays pending approval (claude_driver.py:419-422)."""
    assert BUILD_COMPLETE_TOOL == f"mcp__{PROTO_MCP_SERVER_NAME}__build_complete"


async def test_complete_emits_a_build_complete_event(tmp_path):
    _prototype_dir(tmp_path).joinpath("index.html").write_text("<h1>hi</h1>")
    seen: list[AgentEvent] = []
    handler = _handler(tmp_path, seen.append)

    await handler({"summary": "할 일 앱을 만들었다", "remaining": "다크 모드",
                   "changes": "로그인 화면 제외"})

    assert len(seen) == 1
    ev = seen[0]
    assert ev.kind == "build_complete"
    payload = json.loads(ev.payload)
    assert payload == {"summary": "할 일 앱을 만들었다", "remaining": "다크 모드",
                       "changes": "로그인 화면 제외"}


async def test_remaining_is_optional(tmp_path):
    _prototype_dir(tmp_path).joinpath("index.html").write_text("x")
    seen: list[AgentEvent] = []
    handler = _handler(tmp_path, seen.append)

    await handler({"summary": "완성", "changes": "없음"})

    assert json.loads(seen[0].payload)["remaining"] == ""


async def test_completion_is_refused_when_prototype_dir_is_missing(tmp_path):
    """Keeps the tool from declaring a falsehood — the same discipline as the old submit_document
    (agent/tools.py). The returned string must be something the agent can read and fix on its own."""
    seen: list[AgentEvent] = []
    handler = _handler(tmp_path, seen.append)   # no prototype/

    result = await handler({"summary": "다 했다", "changes": "없음"})

    assert seen == []                            # no event is emitted
    text = result["content"][0]["text"]
    assert "prototype/" in text


async def test_completion_is_refused_when_prototype_dir_is_empty(tmp_path):
    _prototype_dir(tmp_path)                     # created but empty
    seen: list[AgentEvent] = []
    handler = _handler(tmp_path, seen.append)

    result = await handler({"summary": "다 했다", "changes": "없음"})

    assert seen == []
    assert "prototype/" in result["content"][0]["text"]


async def test_a_successful_completion_returns_text_for_the_agent(tmp_path):
    _prototype_dir(tmp_path).joinpath("index.html").write_text("x")
    handler = _handler(tmp_path, lambda ev: None)

    result = await handler({"summary": "완성", "changes": "없음"})

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

    result = await handler({"summary": "다 만들었다", "changes": "없음"})

    assert "aipds-theme.css" in result["content"][0]["text"]
    # A rejection does not end the session — no event must be emitted.
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

    await handler({"summary": "다 만들었다", "changes": "없음"})

    assert [e.kind for e in seen] == ["build_complete"]


async def test_build_complete_skips_the_theme_check_when_the_profile_has_no_tokens(tmp_path):
    """A 0-token profile does not force the theme import — there are no values to force.

    This is an **intended limitation**: in that case the brand travels only through the DESIGN.md prose
    (design_rules(has_tokens=False) instructs moving it into globals.css), and we have no cheap way
    to check "whether it was moved". Rather than letting something uncheckable pass,
    it is not passed falsely — the root theme file is the no-profile stub, so
    no file claiming "브랜드 적용됨" (brand applied) is left in the workspace.
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

    await handler({"summary": "다 만들었다", "changes": "없음"})

    assert [e.kind for e in seen] == ["build_complete"]


async def test_build_complete_skips_the_theme_check_without_a_profile(tmp_path):
    (tmp_path / "prototype").mkdir()
    (tmp_path / "prototype" / "package.json").write_text("{}", encoding="utf-8")

    seen: list[AgentEvent] = []
    handler = _handler(tmp_path, seen.append)

    await handler({"summary": "다 만들었다", "changes": "없음"})

    assert [e.kind for e in seen] == ["build_complete"]


async def test_completion_is_refused_without_changes(tmp_path):
    """Emptying changes makes 'nothing changed' indistinguishable from 'not recorded' -- reject it, and
    tell the agent what it needs to write so it can fix it."""
    _prototype_dir(tmp_path).joinpath("index.html").write_text("x")
    seen: list[AgentEvent] = []
    handler = _handler(tmp_path, seen.append)

    for blank in ({"summary": "완성"}, {"summary": "완성", "changes": "  "}):
        result = await handler(blank)
        assert "changes" in result["content"][0]["text"]

    assert seen == []


# ---- No completion is accepted if nothing changed since the last completion ----
#
# Every message that comes after completion opens a new improvement session. If a session that only received a
# question declares completion without changing anything, the session closes as soon as it opens and the handoff
# gets overwritten with "코드 안 바꿈" (code not changed).

_ARGS = {"summary": "s", "changes": "없음"}


def _text(result) -> str:
    return result["content"][0]["text"]


async def test_a_completion_with_nothing_changed_since_the_last_one_is_refused(tmp_path):
    page = _prototype_dir(tmp_path) / "index.html"
    page.write_text("v1")
    seen: list[AgentEvent] = []
    handler = _handler(tmp_path, seen.append)

    await handler(_ARGS)
    refused = await handler(_ARGS)

    assert len(seen) == 1                       # the second one has no event
    assert "거부됨" in _text(refused)
    assert "채팅" in _text(refused)              # tells where to write the answer

    page.write_text("v2")
    await handler(_ARGS)
    assert len(seen) == 2


async def test_a_file_removed_without_the_edit_tools_still_counts_as_a_change(tmp_path):
    """The decision is by file content — an improvement that deleted a screen with Bash `rm` is still an improvement."""
    proto = _prototype_dir(tmp_path)
    (proto / "index.html").write_text("x")
    (proto / "old-page.tsx").write_text("y")
    seen: list[AgentEvent] = []
    handler = _handler(tmp_path, seen.append)
    await handler(_ARGS)

    (proto / "old-page.tsx").unlink()
    await handler(_ARGS)

    assert len(seen) == 2


async def test_install_and_build_byproducts_are_not_a_change(tmp_path):
    """Files rewritten by hosting's `npm install`/build and by type checking are not source changes."""
    proto = _prototype_dir(tmp_path)
    (proto / "index.html").write_text("x")
    seen: list[AgentEvent] = []
    handler = _handler(tmp_path, seen.append)
    await handler(_ARGS)

    (proto / "package-lock.json").write_text("{}")
    (proto / "tsconfig.tsbuildinfo").write_text("{}")
    (proto / "next-env.d.ts").write_text("//")
    (proto / ".next").mkdir()
    (proto / ".next" / "build-manifest.json").write_text("{}")
    refused = await handler(_ARGS)

    assert len(seen) == 1
    assert "거부됨" in _text(refused)
