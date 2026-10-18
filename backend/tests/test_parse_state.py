# backend/tests/test_parse_state.py
from pathlib import Path
from aipds.parsers.state import parse_state_file

FIX = Path(__file__).parent / "fixtures"

def test_parses_pilot1_state():
    st = parse_state_file((FIX / "aiplc-state.md").read_text(encoding="utf-8"))
    assert st.project_type == "Greenfield"
    names = [s.name for s in st.stages]
    assert "Workspace Detection" in names
    assert all(s.status == "completed" for s in st.stages)  # ws-m finished all stages

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


# ---- HTML entities (observed 2026-08-18: ws-d) ----
# The model sent `"stage": "Prototype &amp; Validation"` to report_stage, and it was
# saved as is in aiplc-state.md and shown in the sidebar as `&amp;`. Neither the ruleset
# nor our code contains `&amp;`; the escaping comes from the model. The name is a key, so
# not just the display but also matching breaks.

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
    """The value of normalizing lies here, not in display: if the escaped name does not match
    its own line, the in-progress stage resolves to nothing."""
    md = (
        "# AI-PLC State\n"
        "- **Current Stage**: Prototype &amp; Validation\n"
        "## Stage Progress\n"
        "- [x] Envision\n"
        "- [ ] Prototype & Validation\n"      # mixed state: fine in the file, broken only in Current Stage
    )
    st = parse_state_file(md)
    in_prog = [s.name for s in st.stages if s.status == "in_progress"]
    assert in_prog == ["Prototype & Validation"]


def test_normalizes_the_other_xml_entities_and_trims():
    from aipds.parsers.state import normalize_stage_name
    assert normalize_stage_name("  Go-to-Market  ") == "Go-to-Market"
    assert normalize_stage_name("A &lt;B&gt; C") == "A <B> C"
    assert normalize_stage_name("&quot;X&quot;") == '"X"'
    # A single sub, so there is no replacement-order accident.
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


# ---- Checkboxes are stages only inside `## Stage Progress` ----
# Observed 2026-08-18 (test12345678): the sidebar showed 14 items: 6 stages + 8 sub-steps
# from `## Envision 진행 내역` (Envision progress log), which the agent created as its own
# ledger. Previously `report_stage` upserted the state file by our own hand and touched
# only that section (state_sync.upsert_stage), but once that tool was replaced by a hook
# and the agent became the sole writer of the file, nothing was left reading selectively.

_REAL_SHAPE = (
    "# AI-PLC State Tracking\n"
    "\n"
    "## Project Information\n"
    "- **Project Type**: Greenfield\n"
    "- **Current Stage**: DISCOVERY - Envision\n"
    "\n"
    "## Stage Progress\n"
    "### 🟣 DISCOVERY PHASE\n"
    "- [x] Workspace Detection\n"
    "- [x] Discovery Mode Selection (Path A 선택)\n"
    "- [ ] Envision\n"
    "- [ ] Solution Analysis\n"
    "- [ ] Product Strategy\n"
    "- [ ] Go-to-Market\n"
    "\n"
    "## Envision 진행 내역\n"
    "- [x] Step 0.1 — 사업 컨텍스트 입력 방식 선택\n"
    "- [x] Step 0.2 — 사업 컨텍스트 수집\n"
    "- [ ] Step 1 — Pain Point 입력 방식 결정\n"
    "- [ ] Step 2 — Pain Point 수집\n"
)


def test_sub_step_checklists_outside_the_section_are_not_stages():
    st = parse_state_file(_REAL_SHAPE)
    assert [s.name for s in st.stages] == [
        "Workspace Detection", "Discovery Mode Selection (Path A 선택)",
        "Envision", "Solution Analysis", "Product Strategy", "Go-to-Market",
    ]


def test_a_sub_heading_does_not_close_the_section():
    """The upstream template puts `### 🟣 DISCOVERY PHASE` inside the section
    (envision.md:420-425). Closing the section at `###` would make every stage disappear."""
    st = parse_state_file(_REAL_SHAPE)
    assert len(st.stages) == 6, [s.name for s in st.stages]


def test_current_stage_still_resolves_with_a_phase_prefix():
    """Even with a prefix, as in `DISCOVERY - Envision`, the substring fallback must find its
    own line; otherwise the in-progress stage resolves to nothing."""
    st = parse_state_file(_REAL_SHAPE)
    assert [s.name for s in st.stages if s.status == "in_progress"] == ["Envision"]


def test_falls_back_to_the_whole_document_without_the_section():
    """A document without the section is scanned with the old behavior. An empty sidebar is
    worse than one with stray items mixed in; it is the same kind of failure as questions
    vanishing when question parsing failed silently."""
    st = parse_state_file(
        "# AI-PLC State\n"
        "- **Current Stage**: Envision\n"
        "- [x] Workspace Detection\n"
        "- [ ] Envision\n"
    )
    assert [s.name for s in st.stages] == ["Workspace Detection", "Envision"]
    assert [s.status for s in st.stages] == ["completed", "in_progress"]


def test_a_section_that_is_empty_yields_no_stages():
    """This is the upstream template's initial state: `[Will be populated as workflow progresses]`.
    If the fallback kicked in here, it would read the checkboxes in the rest of the document as stages."""
    st = parse_state_file(
        "# AI-PLC State Tracking\n"
        "- **Current Stage**: Workspace Detection\n"
        "## Stage Progress\n"
        "[Will be populated as workflow progresses]\n"
        "\n"
        "## Notes\n"
        "- [x] 이건 스테이지가 아니다\n"
    )
    assert st.stages == []


def test_a_decorated_section_heading_is_still_the_section():
    """**With exact matching, this fix would silently be undone.**

    If the heading is decorated, the section is not found, the fallback kicks in, and the
    whole document becomes stages. Decorating headings is an observed habit of the agent
    (`### 🟣 DISCOVERY PHASE`, `## Envision 진행 내역`).
    """
    for heading in ("## Stage Progress",
                    "## Stage Progress (Discovery)",
                    "## Stage Progress — DISCOVERY PHASE",
                    "## 🟣 Stage Progress",
                    "## stage progress"):
        st = parse_state_file(
            "# S\n"
            "- **Current Stage**: Envision\n"
            f"{heading}\n"
            "- [x] Workspace Detection\n"
            "- [ ] Envision\n"
            "\n"
            "## Envision 진행 내역\n"
            "- [x] Step 0.1 — a\n"
            "- [ ] Step 1 — b\n"
        )
        assert [s.name for s in st.stages] == ["Workspace Detection", "Envision"], heading


def test_indented_sub_steps_under_a_stage_are_not_stages():
    """The agent writes steps indented under a stage (observed: 5 stages were read as 18).
    An indented checkbox is that stage's ledger, not a stage."""
    md = """# State
- **Current Stage**: Product Strategy

## Stage Progress
### DISCOVERY PHASE
- [x] Envision
  - [x] Step 0: Business Context Gathering
  - [x] Step 1: Pain Point Input Mode Selection
- [x] Solution Analysis
- [ ] Product Strategy
    - [ ] Step 1: Strategy Questions
"""
    state = parse_state_file(md)
    assert [s.name for s in state.stages] == ["Envision", "Solution Analysis",
                                             "Product Strategy"]
    assert state.stages[-1].status == "in_progress"


def test_a_fully_indented_list_keeps_its_stages():
    md = """## Stage Progress
  - [x] Envision
  - [ ] Solution Analysis
"""
    assert [s.name for s in parse_state_file(md).stages] == ["Envision",
                                                            "Solution Analysis"]


def test_a_status_emoji_starts_the_note_not_the_name():
    md = """## Stage Progress
- [x] Envision ✅ 승인 완료
- [x] Solution Analysis ✅ Branch A.1 (단일 솔루션)
"""
    stages = parse_state_file(md).stages
    assert [(s.name, s.note) for s in stages] == [
        ("Envision", "✅ 승인 완료"),
        ("Solution Analysis", "✅ Branch A.1 (단일 솔루션)")]
