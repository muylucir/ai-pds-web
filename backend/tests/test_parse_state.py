# backend/tests/test_parse_state.py
from pathlib import Path
from pathfinder.parsers.state import parse_state_file

FIX = Path(__file__).parent / "fixtures"

def test_parses_pilot1_state():
    st = parse_state_file((FIX / "aiplc-state.md").read_text(encoding="utf-8"))
    assert st.project_type == "Greenfield"
    names = [s.name for s in st.stages]
    assert "Workspace Detection" in names
    assert all(s.status == "completed" for s in st.stages)  # pilot1 finished all stages

def test_pending_and_note_split():
    md = (
        "# AI-PLC State Tracking\n"
        "- **Project Type**: Greenfield\n"
        "- **Current Stage**: Envision\n"
        "## Stage Progress\n"
        "- [x] Workspace Detection — Completed 2026-07-04\n"
        "- [ ] Envision\n"
    )
    st = parse_state_file(md)
    ws = next(s for s in st.stages if s.name == "Workspace Detection")
    assert ws.status == "completed"
    assert ws.note == "Completed 2026-07-04"
    env = next(s for s in st.stages if s.name == "Envision")
    assert env.status == "in_progress"  # matches Current Stage, not yet completed
