from pathlib import Path
import pytest
from pathfinder.models import AgentEvent
from pathfinder.agent.tools import build_tools


def _tool_by_name(tools, name):
    # claude_agent_sdk의 SdkMcpTool은 .name을 노출하고, .handler가 실제 async
    # 구현이다 — strands @tool 객체(.tool_name, 직접 호출 가능)와 다르다.
    return next(t for t in tools if getattr(t, "name", "") == name)


def _tools(workspace):
    emitted = []
    tools = build_tools(str(workspace), emitted.append)
    return {name: _tool_by_name(tools, name)
            for name in ("report_stage", "submit_document",
                         "handoff_prototype")}, emitted


async def _call(tool, **kwargs):
    """SdkMcpTool.handler는 async이고 단일 dict 인자를 받는다."""
    result = await tool.handler(kwargs)
    return result["content"][0]["text"]


async def test_report_stage_rejects_invalid_status(tmp_path):
    tools, _ = _tools(tmp_path / "ws")
    out = await _call(tools["report_stage"], stage="Envision", status="bogus")
    assert "invalid status" in out


async def test_report_stage_writes_state_file(tmp_path):
    from pathfinder.parsers.state import parse_state_file
    ws = tmp_path / "ws"; ws.mkdir()
    tools, _ = _tools(ws)
    await _call(tools["report_stage"], stage="Envision", status="in_progress", summary="시작")
    state_file = ws / "aiplc-docs" / "aiplc-state.md"
    assert state_file.is_file()
    state = parse_state_file(state_file.read_text(encoding="utf-8"))
    assert state.current_stage == "Envision"
    await _call(tools["report_stage"], stage="Envision", status="completed", summary="끝")
    state = parse_state_file(state_file.read_text(encoding="utf-8"))
    assert state.stages[0].status == "completed"


async def test_report_stage_survives_state_write_failure(tmp_path, monkeypatch):
    # fail-soft: 상태 파일 upsert가 터져도 이벤트/반환은 정상.
    ws = tmp_path / "ws"; ws.mkdir()
    emitted = []
    from pathfinder.agent import tools as tools_mod
    monkeypatch.setattr(tools_mod, "upsert_stage",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    tools = {t.name: t for t in tools_mod.build_tools(str(ws), emitted.append)}
    out = await _call(tools["report_stage"], stage="Envision", status="in_progress")
    assert "stage recorded" in out
    assert emitted and emitted[0].kind == "stage"


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


# ---- handoff_prototype — 빌드로 넘기는 행동 ----
# 2026-08-17: Path A.1의 Step 3은 "Build Prototype"이고 상류 Step 4~11은 돌아가는
# 프로토타입을 전제한다. Pathfinder는 빌드를 Prototypes 탭이 하므로 그 자리에서
# 흐름이 끊겼다 — 그런데 **금지만 있고 대체 행동이 없어서** 에이전트가 즉흥
# 대응했다(실측 keumkang-v5: 자격증명 점검 → API 키 요구 → 선행 조건 3건 나열,
# Prototypes 탭 안내는 한 번도 없었다).
#
# report_stage가 있어서 상태 파일을 손으로 안 쓰고, submit_document가 있어서 문서
# 준비를 선언하는 것과 같은 규율이다: **도구가 행동을 만든다.** 이 도구가
# "빌드로 넘어가기"라는 하고 싶은 일에 대응한다.

async def test_handoff_refuses_when_the_spec_is_not_there(tmp_path):
    """submit_document와 같은 규율 — 도구가 거짓을 선언할 수 없다. 명세가 없으면
    Prototypes 탭에 카드가 없으므로, 넘겼다고 말하면 사용자가 빈 탭을 본다."""
    tools, emitted = _tools(tmp_path / "ws")
    out = await _call(tools["handoff_prototype"], slug="prototype")
    assert "거부" in out or "Refused" in out
    assert not emitted, "거부했는데 이벤트를 흘리면 화면에 카드가 뜬다"


async def test_handoff_accepts_the_single_prototype_layout(tmp_path):
    """Path A.1의 명세 경로다(proto/layout.py의 SINGLE_SPEC_KEY)."""
    ws = tmp_path / "ws"
    spec = ws / "aiplc-docs" / "discovery" / "prototype" / "prototype-spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# 명세", encoding="utf-8")

    tools, emitted = _tools(ws)
    out = await _call(tools["handoff_prototype"], slug="prototype")

    assert "Prototypes" in out
    kinds = [e.kind for e in emitted]
    assert "prototype_ready" in kinds, kinds


async def test_handoff_accepts_the_slugged_layout(tmp_path):
    ws = tmp_path / "ws"
    spec = (ws / "aiplc-docs" / "discovery" / "prototypes" / "maint"
            / "PROTOTYPE-maint.md")
    spec.parent.mkdir(parents=True)
    spec.write_text("# 명세", encoding="utf-8")

    tools, emitted = _tools(ws)
    await _call(tools["handoff_prototype"], slug="maint")

    payloads = [e.payload for e in emitted if e.kind == "prototype_ready"]
    assert payloads and "maint" in payloads[0]


async def test_handoff_tells_the_agent_to_stop_and_not_ask_for_credentials(tmp_path):
    """반환 문자열이 다음 행동을 지정한다. 이것이 없으면 에이전트가 Step 4로
    계속 가거나(돌아가는 프로토타입이 없으니 실패한다) 자격증명을 묻는다 —
    프로젝트가 모델과 자격증명을 이미 갖고 있는데도."""
    ws = tmp_path / "ws"
    spec = ws / "aiplc-docs" / "discovery" / "prototype" / "prototype-spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# 명세", encoding="utf-8")

    tools, _ = _tools(ws)
    out = await _call(tools["handoff_prototype"], slug="prototype")

    assert "턴" in out or "end your turn" in out.lower()
    assert "자격증명" in out or "credential" in out.lower()


async def test_handoff_refuses_a_path_escaping_slug(tmp_path):
    tools, emitted = _tools(tmp_path / "ws")
    out = await _call(tools["handoff_prototype"], slug="../../etc")
    assert "거부" in out or "Refused" in out
    assert not emitted
