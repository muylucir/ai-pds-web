# backend/tests/test_driver_reconcile_wiring.py — do the hook and the turn boundary call reconciliation?
#
# tests/test_agent_reconcile.py checks the **verdict** (file → event). This file checks the
# **wiring**: the two places where that verdict is actually called — the PostToolUse hook and just before
# `_pump`'s terminal emission — and whether the two share a cursor.
#
# Wiring is checked separately because this feature's failure shape was not "the verdict was wrong" but
# "the verdict was never called". The old `report_stage` tool's verdict was accurate too — the model
# just did not call it (agent/reconcile.py header).
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aipds.agent.claude_driver import ClaudeDriver
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


# ---- Hook: a state file write becomes a stage event ----


@pytest.mark.asyncio
async def test_writing_the_state_file_emits_stage_events(tmp_path):
    d, ws = _driver(tmp_path)
    payload = _write(ws, "aiplc-docs/aiplc-state.md", STATE_MD)
    out = await d._on_post_tool_use(payload, "t1", None)
    # It does not cut the turn — state updates happen in the middle of work.
    assert out == {}
    assert [p["stage"] for p in _kinds(d, "stage")] == [
        "Workspace Detection", "Envision"]


@pytest.mark.asyncio
async def test_the_state_file_also_emits_file_changed(tmp_path):
    """It is also an artifact, so the document panel path is not lost."""
    d, ws = _driver(tmp_path)
    await d._on_post_tool_use(
        _write(ws, "aiplc-docs/aiplc-state.md", STATE_MD), "t1", None)
    assert "aiplc-docs/aiplc-state.md" in [
        e.path for e in d._queue if e.kind == "file_changed"]


@pytest.mark.asyncio
async def test_rewriting_the_state_file_unchanged_emits_nothing_new(tmp_path):
    """The hook fires on every write, so the diff cursor must stay alive on the hook path too —
    otherwise a normal turn that writes the state file twice doubles the sidebar."""
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


# ---- Hook: a build-instructions.md write becomes a handoff + turn end ----


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
    # Discovery's job ends here, and the next action belongs to the user.
    assert out["continue_"] is False
    reason = out["stopReason"]
    # It **specifies** the next action — otherwise the agent carries on to upstream Step 4 or asks for credentials
    # (observed in ws-a-v5).
    assert "Prototypes" in reason
    assert "prototype" in reason


@pytest.mark.asyncio
async def test_build_instructions_without_a_spec_do_not_stop_the_turn(tmp_path):
    """Without a spec, no card can be made (routes/prototypes.py). Cutting the turn here would stop
    the agent with no chance to write the spec — the user sees an empty tab."""
    d, ws = _driver(tmp_path)
    out = await d._on_post_tool_use(
        _write(ws, "aiplc-docs/discovery/prototype/build-instructions.md"),
        "t1", None)
    assert out == {}
    assert _kinds(d, "prototype_ready") == []


@pytest.mark.asyncio
async def test_rewriting_build_instructions_does_not_stop_the_turn_twice(tmp_path):
    """Rewriting the build instructions is normal behavior. Cutting again on the second write would
    trap the agent in the same spot, and two cards would appear."""
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


# ---- Turn boundary: recovers what the hook missed ----


def test_the_turn_boundary_recovers_a_state_file_written_outside_the_hook(tmp_path):
    """**This is the core of item 1.** PostToolUse is attached only to `Write|Edit|MultiEdit`, so
    it does not see files written with Bash (the same limit as in the discovery_guard.py header). Reading
    the disk once here covers both that bypass and batch drops — the lost `report_stage` in
    test123456 on 2026-08-18 was that case.
    """
    d, ws = _driver(tmp_path)
    p = ws / "aiplc-docs" / "aiplc-state.md"
    p.parent.mkdir(parents=True)
    p.write_text(STATE_MD, encoding="utf-8")   # a write that did not go through the hook

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
    """The two places share the cursor, so reconciliation usually does nothing. If they did not share it,
    every stage would flow twice per turn and the sidebar would double."""
    d, ws = _driver(tmp_path)
    import asyncio
    asyncio.run(d._on_post_tool_use(
        _write(ws, "aiplc-docs/aiplc-state.md", STATE_MD), "t1", None))
    before = len(_kinds(d, "stage"))
    d._reconcile_turn()
    assert len(_kinds(d, "stage")) == before


def test_the_turn_boundary_survives_an_unreadable_workspace(tmp_path):
    """Reconciliation is a backstop — if it failed the turn, it would be a new cause of failure, not a backstop
    (the same judgment as runner._sync_abandoned_turn)."""
    d, _ = _driver(tmp_path)
    d._workspace = "/nonexistent/nowhere"
    d._reconcile_turn()          # no exception leaks out
    assert _kinds(d, "stage") == []


# ---- End to end: reconciliation events arrive **before** `done` ----
#
# The position is the contract. `frontend/lib/api/sse.ts:29` closes the EventSource on `done`, so
# events after it never reach `onEvent` — a `stage` attached after `done` silently
# disappears and the sidebar goes stale (invariant 1 of claude_driver._pump).


def _scripted_driver(tmp_path, scripted):
    """The real `run()` path running one script. Same shape as the helper in tests/test_claude_driver.py,
    but it needs only the rules fixture."""
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
    p.write_text(STATE_MD, encoding="utf-8")   # a write that did not go through the hook

    events = [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    kinds = [e.kind for e in events]

    assert "stage" in kinds, kinds
    # There is exactly one terminal event, and it is last.
    assert kinds[-1] == "done"
    assert kinds.count("done") == 1
    assert kinds.index("stage") < kinds.index("done")


@pytest.mark.asyncio
async def test_a_turn_with_nothing_to_reconcile_is_unchanged(tmp_path):
    """Reconciliation adds no events to a normal turn — it is quiet on an empty workspace."""
    d, _ = _scripted_driver(tmp_path, {"text": ["ok"]})
    events = [ev async for ev in d.run("hi", {"session_id": "s-1"})]
    assert [e.kind for e in events if e.kind == "stage"] == []
    assert [e.kind for e in events][-1] == "done"


# ---- Hook: an artifact write becomes a document event (the old submit_document) ----
#
# The old tool's failure shape was silence, not the verdict — the frontend wrote down the observation:
# "에이전트는 대부분의 문서를 submit_document 없이 file_write로만 만든다" (the agent creates most documents with file_write alone, without submit_document)
# (useWorkspaceStream.ts:177). So the wiring is checked.


@pytest.mark.asyncio
async def test_a_document_write_emits_a_document_event(tmp_path):
    d, ws = _driver(tmp_path)
    out = await d._on_post_tool_use(
        _write(ws, "aiplc-docs/discovery/envision/prfaq.md", "# PR/FAQ\n본문\n"),
        "t1", None)
    # Document writes do not cut the turn — they happen in the middle of work.
    assert out == {}
    assert _kinds(d, "document") == [
        {"path": "aiplc-docs/discovery/envision/prfaq.md",
         "version": "1", "summary": ""}]


@pytest.mark.asyncio
async def test_rewriting_a_document_unchanged_emits_nothing_new(tmp_path):
    """The hook fires on every write, so the cursor must stay alive on the hook path — otherwise
    a normal turn that writes the same document twice shows the update banner twice."""
    d, ws = _driver(tmp_path)
    payload = _write(ws, "aiplc-docs/discovery/prfaq.md", "# PR/FAQ\n본문\n")
    await d._on_post_tool_use(payload, "t1", None)
    await d._on_post_tool_use(payload, "t2", None)
    assert len(_kinds(d, "document")) == 1


@pytest.mark.asyncio
async def test_a_record_keeping_write_emits_no_document_event(tmp_path):
    d, ws = _driver(tmp_path)
    for rel, text in (("aiplc-docs/aiplc-state.md", STATE_MD),
                      ("aiplc-docs/audit.md", "- 기록\n"),
                      ("aiplc-docs/discovery/envision/p-questions.md", "## Q1\n")):
        await d._on_post_tool_use(_write(ws, rel, text), "t1", None)
    assert _kinds(d, "document") == []


@pytest.mark.asyncio
async def test_the_turn_boundary_recovers_a_document_the_hook_never_saw(tmp_path):
    """Backstop. The hook is attached only to `Write|Edit|MultiEdit`, so it does not see documents written with Bash —
    the same reason stages and handoffs are in `_reconcile_turn`."""
    d, ws = _driver(tmp_path)
    p = ws / "aiplc-docs" / "discovery" / "prfaq.md"
    p.parent.mkdir(parents=True)
    p.write_text("# PR/FAQ\n본문\n", encoding="utf-8")  # does not go through the hook
    d._reconcile_turn()
    assert [x["path"] for x in _kinds(d, "document")] == [
        "aiplc-docs/discovery/prfaq.md"]


@pytest.mark.asyncio
async def test_the_hook_and_the_turn_boundary_share_the_document_cursor(tmp_path):
    """If the two paths did not share the cursor, the turn boundary would re-announce a document the hook already
    announced (the same contract as stages passing through the single `_emit_stage_events`)."""
    d, ws = _driver(tmp_path)
    await d._on_post_tool_use(
        _write(ws, "aiplc-docs/discovery/prfaq.md", "# PR/FAQ\n본문\n"), "t1", None)
    before = len(_kinds(d, "document"))
    assert before == 1, "훅이 아무것도 흘리지 않았으면 아래 단정이 공허하다"
    d._reconcile_turn()
    assert len(_kinds(d, "document")) == before


@pytest.mark.asyncio
async def test_a_document_changed_between_turns_is_still_announced(tmp_path):
    """Seeding happens **only once, on the first turn**. Seeding every turn would silently swallow documents
    changed between turns — writes from another instance, or restored contents that differ from local."""
    d, ws = _driver(tmp_path)
    p = ws / "aiplc-docs" / "discovery" / "prfaq.md"
    p.parent.mkdir(parents=True)
    p.write_text("# PR/FAQ\n초안\n", encoding="utf-8")
    d._seed_document_cursor()          # first turn: treat what exists as seen
    assert _kinds(d, "document") == []

    p.write_text("# PR/FAQ\n고친 본문\n", encoding="utf-8")
    d._seed_document_cursor()          # second turn: must be a no-op
    d._reconcile_turn()
    assert [x["version"] for x in _kinds(d, "document")] == ["2"]
