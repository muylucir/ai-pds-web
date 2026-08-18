# backend/tests/test_driver_reconcile_wiring.py — 훅과 턴 경계가 재조정을 부르는가.
#
# tests/test_agent_reconcile.py는 **판정**을 검사한다(파일 → 이벤트). 이 파일은
# **배선**을 검사한다: 그 판정이 실제로 불리는 두 자리 — PostToolUse 훅과 `_pump`의
# 종결 배출 직전 — 그리고 두 자리가 커서를 공유하는가.
#
# 배선을 따로 검사하는 이유는 이 기능의 실패 모양이 "판정이 틀렸다"가 아니라
# "판정이 안 불렸다"였기 때문이다. 옛 `report_stage` 도구도 판정은 정확했다 — 모델이
# 부르지 않았을 뿐이다(agent/reconcile.py 헤더).
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pathfinder.agent.claude_driver import ClaudeDriver
from tests.fakes.in_memory_s3 import FakeS3Store

STATE_MD = ("# AI-PLC State\n\n"
            "- **Current Stage**: Envision\n\n"
            "## Stage Progress\n"
            "- [x] Workspace Detection\n"
            "- [ ] Envision\n")


def _driver(tmp_path: Path) -> tuple[ClaudeDriver, Path]:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    d = ClaudeDriver(workspace=str(ws), rules_dir=str(tmp_path / "rules"),
                     config_dir=str(tmp_path / "cfg"), s3=FakeS3Store(),
                     client_factory=lambda session: None)
    return d, ws


def _write(ws: Path, rel: str, text: str = "x") -> dict:
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return {"tool_name": "Write", "tool_input": {"file_path": str(p)}}


def _kinds(d: ClaudeDriver, kind: str) -> list[dict]:
    return [json.loads(e.payload) for e in d._queue if e.kind == kind]


# ---- 훅: 상태 파일 쓰기가 stage 이벤트가 된다 ----


@pytest.mark.asyncio
async def test_writing_the_state_file_emits_stage_events(tmp_path):
    d, ws = _driver(tmp_path)
    payload = _write(ws, "aiplc-docs/aiplc-state.md", STATE_MD)
    out = await d._on_post_tool_use(payload, "t1", None)
    # 턴을 끊지 않는다 — 상태 갱신은 작업 도중에 일어나는 일이다.
    assert out == {}
    assert [p["stage"] for p in _kinds(d, "stage")] == [
        "Workspace Detection", "Envision"]


@pytest.mark.asyncio
async def test_the_state_file_also_emits_file_changed(tmp_path):
    """산출물이기도 하므로 문서 패널 경로를 잃지 않는다."""
    d, ws = _driver(tmp_path)
    await d._on_post_tool_use(
        _write(ws, "aiplc-docs/aiplc-state.md", STATE_MD), "t1", None)
    assert "aiplc-docs/aiplc-state.md" in [
        e.path for e in d._queue if e.kind == "file_changed"]


@pytest.mark.asyncio
async def test_rewriting_the_state_file_unchanged_emits_nothing_new(tmp_path):
    """훅이 매 쓰기에 발동하므로 diff 커서가 훅 경로에서도 살아 있어야 한다 —
    아니면 상태 파일을 두 번 쓰는 정상 턴이 사이드바를 두 배로 만든다."""
    d, ws = _driver(tmp_path)
    payload = _write(ws, "aiplc-docs/aiplc-state.md", STATE_MD)
    await d._on_post_tool_use(payload, "t1", None)
    before = len(_kinds(d, "stage"))
    await d._on_post_tool_use(payload, "t2", None)
    assert len(_kinds(d, "stage")) == before


@pytest.mark.asyncio
async def test_a_normal_document_write_emits_no_stage_events(tmp_path):
    d, ws = _driver(tmp_path)
    await d._on_post_tool_use(
        _write(ws, "aiplc-docs/discovery/envision/prfaq.md", "# PR/FAQ"),
        "t1", None)
    assert _kinds(d, "stage") == []


# ---- 훅: build-instructions.md 쓰기가 인계 + 턴 종료가 된다 ----


def _handoff_ready(ws: Path) -> None:
    (ws / "aiplc-docs/discovery/prototype").mkdir(parents=True, exist_ok=True)
    (ws / "aiplc-docs/discovery/prototype/prototype-spec.md").write_text(
        "# spec", encoding="utf-8")


@pytest.mark.asyncio
async def test_writing_build_instructions_hands_off_and_stops_the_turn(tmp_path):
    d, ws = _driver(tmp_path)
    _handoff_ready(ws)
    out = await d._on_post_tool_use(
        _write(ws, "aiplc-docs/discovery/prototype/build-instructions.md"),
        "t1", None)
    assert _kinds(d, "prototype_ready") == [{
        "slug": "prototype",
        "spec_path": "aiplc-docs/discovery/prototype/prototype-spec.md"}]
    # 여기서 Discovery의 일이 끝나고 다음 행동은 사용자의 것이다.
    assert out["continue_"] is False
    reason = out["stopReason"]
    # 다음 행동을 **지정한다** — 없으면 상류 Step 4로 계속 가거나 자격증명을 묻는다
    # (실측 keumkang-v5).
    assert "Prototypes" in reason
    assert "prototype" in reason


@pytest.mark.asyncio
async def test_build_instructions_without_a_spec_do_not_stop_the_turn(tmp_path):
    """명세가 없으면 카드를 만들 수 없다(routes/prototypes.py). 여기서 턴을 끊으면
    에이전트가 명세를 쓸 기회 없이 멈춘다 — 사용자는 빈 탭을 본다."""
    d, ws = _driver(tmp_path)
    out = await d._on_post_tool_use(
        _write(ws, "aiplc-docs/discovery/prototype/build-instructions.md"),
        "t1", None)
    assert out == {}
    assert _kinds(d, "prototype_ready") == []


@pytest.mark.asyncio
async def test_rewriting_build_instructions_does_not_stop_the_turn_twice(tmp_path):
    """빌드 지시를 고쳐 쓰는 것은 정상 행동이다. 두 번째 쓰기에서 또 끊으면
    에이전트가 같은 자리에 갇히고, 카드도 두 장 뜬다."""
    d, ws = _driver(tmp_path)
    _handoff_ready(ws)
    payload = _write(ws, "aiplc-docs/discovery/prototype/build-instructions.md")
    first = await d._on_post_tool_use(payload, "t1", None)
    assert first["continue_"] is False
    second = await d._on_post_tool_use(payload, "t2", None)
    assert second == {}
    assert len(_kinds(d, "prototype_ready")) == 1


@pytest.mark.asyncio
async def test_the_stop_reason_follows_the_project_language(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    d = ClaudeDriver(workspace=str(ws), rules_dir=str(tmp_path / "rules"),
                     config_dir=str(tmp_path / "cfg"), s3=FakeS3Store(),
                     language="en", client_factory=lambda session: None)
    _handoff_ready(ws)
    out = await d._on_post_tool_use(
        _write(ws, "aiplc-docs/discovery/prototype/build-instructions.md"),
        "t1", None)
    assert not any("가" <= c <= "힣" for c in out["stopReason"])


# ---- 턴 경계: 훅이 못 본 것을 되찾는다 ----


def test_the_turn_boundary_recovers_a_state_file_written_outside_the_hook(tmp_path):
    """**이것이 1번의 핵심이다.** PostToolUse는 `Write|Edit|MultiEdit`에만 붙으므로
    Bash로 쓴 파일을 보지 못한다(discovery_guard.py 헤더의 같은 한계). 여기서
    디스크를 한 번 읽으면 그 우회와 배치 드롭이 함께 덮인다 — 2026-08-18
    test123456의 유실된 `report_stage`가 그 경우다.
    """
    d, ws = _driver(tmp_path)
    p = ws / "aiplc-docs" / "aiplc-state.md"
    p.parent.mkdir(parents=True)
    p.write_text(STATE_MD, encoding="utf-8")   # 훅을 거치지 않은 쓰기

    assert _kinds(d, "stage") == []
    d._reconcile_turn()
    assert [p_["stage"] for p_ in _kinds(d, "stage")] == [
        "Workspace Detection", "Envision"]


def test_the_turn_boundary_recovers_an_unannounced_handoff(tmp_path):
    d, ws = _driver(tmp_path)
    _handoff_ready(ws)
    (ws / "aiplc-docs/discovery/prototype/build-instructions.md").write_text(
        "x", encoding="utf-8")
    d._reconcile_turn()
    assert [p["slug"] for p in _kinds(d, "prototype_ready")] == ["prototype"]


def test_the_turn_boundary_is_quiet_when_the_hook_already_reported(tmp_path):
    """두 자리가 커서를 공유하므로 재조정은 대개 아무 일도 하지 않는다. 공유하지
    않으면 모든 스테이지가 턴마다 두 번 흘러 사이드바가 두 배가 된다."""
    d, ws = _driver(tmp_path)
    import asyncio
    asyncio.run(d._on_post_tool_use(
        _write(ws, "aiplc-docs/aiplc-state.md", STATE_MD), "t1", None))
    before = len(_kinds(d, "stage"))
    d._reconcile_turn()
    assert len(_kinds(d, "stage")) == before


def test_the_turn_boundary_survives_an_unreadable_workspace(tmp_path):
    """재조정은 백스톱이다 — 그것이 턴을 실패시키면 백스톱이 아니라 새 실패 원인이
    된다(runner._sync_abandoned_turn과 같은 판단)."""
    d, _ = _driver(tmp_path)
    d._workspace = "/nonexistent/nowhere"
    d._reconcile_turn()          # 예외가 새어나오지 않는다
    assert _kinds(d, "stage") == []


# ---- 종단: 재조정 이벤트가 `done` **앞에** 도달한다 ----
#
# 위치가 계약이다. `frontend/lib/api/sse.ts:29`가 `done`에서 EventSource를 닫으므로
# 그 뒤의 이벤트는 `onEvent`에 닿지 않는다 — `done` 뒤에 붙은 `stage`는 조용히
# 사라지고 사이드바가 낡는다(claude_driver._pump의 invariant 1).


def _scripted_driver(tmp_path, scripted):
    """대본 하나를 돌리는 실제 `run()` 경로. tests/test_claude_driver.py의 헬퍼와
    같은 모양이지만 룰 픽스처만 필요하다."""
    from tests.fakes.fake_sdk_asking import sdk_client_for

    rules = tmp_path / "rules" / "aws-aiplc-rules"
    rules.mkdir(parents=True)
    (rules / "core-workflow.md").write_text("WORKFLOW", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    d = ClaudeDriver(workspace=str(ws), rules_dir=str(tmp_path / "rules"),
                     config_dir=str(tmp_path / "cfg"), s3=FakeS3Store(),
                     client_factory=lambda session: None)
    d._client_factory = lambda session: sdk_client_for(  # type: ignore[assignment]
        scripted, d._on_can_use_tool)
    return d, ws


@pytest.mark.asyncio
async def test_a_state_file_written_outside_the_hook_reaches_the_stream(tmp_path):
    d, ws = _scripted_driver(tmp_path, {"text": ["ok"]})
    p = ws / "aiplc-docs" / "aiplc-state.md"
    p.parent.mkdir(parents=True)
    p.write_text(STATE_MD, encoding="utf-8")   # 훅을 거치지 않은 쓰기

    events = [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    kinds = [e.kind for e in events]

    assert "stage" in kinds, kinds
    # 종결 이벤트는 정확히 하나이고 마지막이다.
    assert kinds[-1] == "done"
    assert kinds.count("done") == 1
    assert kinds.index("stage") < kinds.index("done")


@pytest.mark.asyncio
async def test_a_turn_with_nothing_to_reconcile_is_unchanged(tmp_path):
    """재조정이 정상 턴에 이벤트를 더하지 않는다 — 빈 워크스페이스에서는 조용하다."""
    d, _ = _scripted_driver(tmp_path, {"text": ["ok"]})
    events = [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    assert [e.kind for e in events if e.kind == "stage"] == []
    assert [e.kind for e in events][-1] == "done"
