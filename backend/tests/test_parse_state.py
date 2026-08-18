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

def test_in_progress_single_on_substring_collision():
    md = (
        "# AI-PLC State Tracking\n"
        "- **Project Type**: Greenfield\n"
        "- **Current Stage**: Discovery Mode Selection\n"
        "## Stage Progress\n"
        "- [ ] Discovery Mode Selection\n"
        "- [ ] Discovery Mode Selection Extended Review\n"
    )
    st = parse_state_file(md)
    in_prog = [s.name for s in st.stages if s.status == "in_progress"]
    assert in_prog == ["Discovery Mode Selection"]  # exactly one, the exact match

def test_in_progress_longest_match_when_no_exact():
    md = (
        "# AI-PLC State Tracking\n"
        "- **Project Type**: Greenfield\n"
        "- **Current Stage**: Discovery Mode Selection Extended Review Phase\n"
        "## Stage Progress\n"
        "- [ ] Discovery\n"
        "- [ ] Discovery Mode Selection Extended Review\n"
    )
    st = parse_state_file(md)
    in_prog = [s.name for s in st.stages if s.status == "in_progress"]
    assert in_prog == ["Discovery Mode Selection Extended Review"]  # longest/most-specific, only one


# ---- HTML 엔티티 (2026-08-18 실측: hpt-sarang) ----
# 모델이 report_stage에 `"stage": "Prototype &amp; Validation"`을 보냈고, 그것이
# 그대로 aiplc-state.md에 저장돼 사이드바에 `&amp;`로 떴다. 룰셋·우리 코드에는
# `&amp;`가 없다 — 모델 쪽 이스케이프다. 이름은 키이므로 표시만이 아니라 매칭도
# 깨진다.

def test_unescapes_entities_in_stage_names():
    md = (
        "# AI-PLC State\n"
        "- **Current Stage**: Prototype &amp; Validation\n"
        "## Stage Progress\n"
        "- [x] Envision\n"
        "- [ ] Prototype &amp; Validation\n"
    )
    st = parse_state_file(md)
    assert st.current_stage == "Prototype & Validation"
    assert [s.name for s in st.stages] == ["Envision", "Prototype & Validation"]


def test_escaped_current_stage_still_matches_its_checklist_line():
    """정규화의 값은 표시가 아니라 여기 있다 — 이스케이프된 이름이 자기 줄과
    매칭되지 않으면 진행 중 스테이지가 아무것도 아니게 된다."""
    md = (
        "# AI-PLC State\n"
        "- **Current Stage**: Prototype &amp; Validation\n"
        "## Stage Progress\n"
        "- [x] Envision\n"
        "- [ ] Prototype & Validation\n"      # 파일에는 정상, Current Stage만 깨진 혼합 상태
    )
    st = parse_state_file(md)
    in_prog = [s.name for s in st.stages if s.status == "in_progress"]
    assert in_prog == ["Prototype & Validation"]


def test_normalizes_the_other_xml_entities_and_trims():
    from pathfinder.parsers.state import normalize_stage_name
    assert normalize_stage_name("  Go-to-Market  ") == "Go-to-Market"
    assert normalize_stage_name("A &lt;B&gt; C") == "A <B> C"
    assert normalize_stage_name("&quot;X&quot;") == '"X"'
    # 한 번의 sub이므로 치환 순서 사고가 없다.
    assert normalize_stage_name("&amp;lt;") == "&lt;"


def test_leaves_ordinary_ampersands_alone():
    st = parse_state_file(
        "# AI-PLC State\n"
        "- **Current Stage**: R&D Review\n"
        "## Stage Progress\n"
        "- [ ] R&D Review\n"
    )
    assert st.current_stage == "R&D Review"
    assert [s.status for s in st.stages] == ["in_progress"]
