# backend/tests/test_agent_tools.py — 남은 커스텀 도구 하나의 계약.
#
# **한때 셋이었다.** `report_stage`와 `handoff_prototype`은 2026-08-18에 PostToolUse
# 훅으로 옮겨 갔다(agent/reconcile.py). 그 둘의 커버리지는 사라지지 않고
# tests/test_agent_reconcile.py로 이동했다 — 옮긴 행동을 옮긴 자리에서 검사한다.
from pathlib import Path
import pytest
from aipds.models import AgentEvent
from aipds.agent.tools import build_tools


def _tool_by_name(tools, name):
    # claude_agent_sdk의 SdkMcpTool은 .name을 노출하고, .handler가 실제 async
    # 구현이다 — strands @tool 객체(.tool_name, 직접 호출 가능)와 다르다.
    return next(t for t in tools if getattr(t, "name", "") == name)


def _tools(workspace):
    emitted = []
    tools = build_tools(str(workspace), emitted.append)
    return {name: _tool_by_name(tools, name)
            for name in ("submit_document",)}, emitted


async def _call(tool, **kwargs):
    """SdkMcpTool.handler는 async이고 단일 dict 인자를 받는다."""
    result = await tool.handler(kwargs)
    return result["content"][0]["text"]


def test_submit_document_is_the_only_custom_tool():
    """도구 집합 자체를 고정한다.

    스테이지와 인계가 훅으로 옮겨 간 뒤 이 목록에 도구가 다시 늘어나는 것은
    "모델이 부르지 않으면 침묵"이라는 실패 경로로 되돌아가는 것이다
    (agent/reconcile.py 헤더에 두 번의 실측이 있다). 새 도구가 정말 필요하면 그
    판단을 여기서 한 번 마주쳐야 한다.
    """
    names = sorted(t.name for t in build_tools("/tmp/ws", lambda e: None))
    assert names == ["submit_document"]


# ---- submit_document must not declare a document that isn't on disk ----

def _ws_and_tools(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    tools, emitted = _tools(ws)
    return ws, tools, emitted


async def test_submit_document_emits_when_the_file_exists(tmp_path):
    ws, tools, emitted = _ws_and_tools(tmp_path)
    doc = ws / "aiplc-docs" / "discovery" / "discovery-document.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# 내용", encoding="utf-8")

    result = await _call(tools["submit_document"],
                         path="aiplc-docs/discovery/discovery-document.md",
                         version="v1", summary="요약")

    assert "submitted" in result
    docs = [e for e in emitted if e.kind == "document"]
    assert len(docs) == 1


async def test_submit_document_refuses_a_path_that_was_never_written(tmp_path):
    """The decoupling that made a real bug invisible: this tool only emitted an
    event, so an agent that called it without a preceding file write produced a
    chat message saying the document was created, a dropdown entry for it, and
    no document. The event is the UI's source of truth for "a document is
    ready", so it must not fire for a file that does not exist."""
    ws, tools, emitted = _ws_and_tools(tmp_path)

    result = await _call(tools["submit_document"],
                         path="aiplc-docs/discovery/discovery-document.md",
                         version="v1")

    assert "document" not in [e.kind for e in emitted]
    assert "저장" in result or "Write" in result  # tells the agent what to do instead


async def test_submit_document_refuses_an_empty_file(tmp_path):
    """A zero-byte or whitespace-only file is the same failure wearing a
    different hat -- the panel would render "문서 내용이 아직 비어 있습니다"
    while the chat claimed success."""
    ws, tools, emitted = _ws_and_tools(tmp_path)
    doc = ws / "aiplc-docs" / "empty.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("   \n", encoding="utf-8")

    result = await _call(tools["submit_document"], path="aiplc-docs/empty.md", version="v1")

    assert "document" not in [e.kind for e in emitted]
    assert "비어" in result or "empty" in result.lower()


async def test_submit_document_rejects_a_path_escaping_the_workspace(tmp_path):
    ws, tools, emitted = _ws_and_tools(tmp_path)
    outside = tmp_path / "secret.md"
    outside.write_text("nope", encoding="utf-8")

    result = await _call(tools["submit_document"], path="../secret.md", version="v1")

    assert "document" not in [e.kind for e in emitted]
    assert "escape" in result.lower() or "경로" in result
