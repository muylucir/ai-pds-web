from pathfinder.agent.state_sync import upsert_stage
from pathfinder.parsers.state import parse_state_file


def test_creates_skeleton_when_no_file():
    md = upsert_stage(None, "Envision", "in_progress")
    state = parse_state_file(md)
    assert state.current_stage == "Envision"
    assert [s.name for s in state.stages] == ["Envision"]
    assert state.stages[0].status == "in_progress"


def test_marks_existing_stage_completed():
    md = upsert_stage(None, "Envision", "in_progress")
    md = upsert_stage(md, "Envision", "completed")
    state = parse_state_file(md)
    assert state.stages[0].status == "completed"


def test_appends_new_stage_to_progress_list():
    md = upsert_stage(None, "Workspace Detection", "completed")
    md = upsert_stage(md, "Envision", "in_progress")
    state = parse_state_file(md)
    assert [s.name for s in state.stages] == ["Workspace Detection", "Envision"]
    assert state.current_stage == "Envision"
    assert state.stages[0].status == "completed"
    assert state.stages[1].status == "in_progress"


def test_completed_does_not_move_current_stage():
    md = upsert_stage(None, "Envision", "in_progress")
    md = upsert_stage(md, "Envision", "completed")
    # Current Stage는 completed로는 안 바뀜 — 다음 in_progress가 갱신
    assert "**Current Stage**: Envision" in md
    md = upsert_stage(md, "Solution Analysis", "in_progress")
    state = parse_state_file(md)
    assert state.current_stage == "Solution Analysis"


def test_matches_stage_by_partial_name_like_parser():
    # 파서와 동일한 관용: 실전 파일은 "Envision (Path A)"처럼 노트가 붙는다.
    existing = """# AI-PLC State
- **Current Stage**: Envision (Path A - Step 1)

## Stage Progress
- [ ] Envision (Path A)
- [ ] Solution Analysis
"""
    md = upsert_stage(existing, "Envision", "completed")
    state = parse_state_file(md)
    envision = next(s for s in state.stages if "Envision" in s.name)
    assert envision.status == "completed"
    # 다른 스테이지는 불변
    assert next(s for s in state.stages if s.name == "Solution Analysis").status == "pending"


def test_preserves_unrelated_content():
    existing = """# AI-PLC State

## Project
- **Name**: TC Copilot

- **Current Stage**: Envision

## Stage Progress
- [ ] Envision

## Notes
- 사용자가 Path A를 선택함.
"""
    md = upsert_stage(existing, "Envision", "completed")
    assert "- **Name**: TC Copilot" in md
    assert "사용자가 Path A를 선택함." in md


def test_roundtrip_with_real_fixture():
    from pathlib import Path
    fixture = (Path(__file__).parent / "fixtures" / "aiplc-state.md").read_text(encoding="utf-8")
    md = upsert_stage(fixture, "Prototype & Validation", "in_progress")
    state = parse_state_file(md)
    target = next(s for s in state.stages if "Prototype" in s.name)
    assert target.status == "in_progress"
    # 나머지 완료 스테이지들은 그대로
    assert sum(1 for s in state.stages if s.status == "completed") >= 5
