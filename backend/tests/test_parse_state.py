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


# ---- 체크박스는 `## Stage Progress` 안에서만 스테이지다 ----
# 2026-08-18 실측(test12345678): 사이드바에 14개가 떴다 — 스테이지 6개 +
# 에이전트가 자기 장부로 만든 `## Envision 진행 내역`의 하위 단계 8개. 예전에는
# `report_stage`가 상태 파일을 우리 손으로 upsert하며 그 섹션만 건드렸는데
# (state_sync.upsert_stage), 그 도구가 훅으로 대체되고 파일을 에이전트가 단독으로
# 쓰게 되면서 가려 읽던 쪽이 없어졌다.

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
    """상류 템플릿이 섹션 안에 `### 🟣 DISCOVERY PHASE`를 둔다
    (envision.md:420-425). `###`에서 섹션을 닫으면 스테이지가 전부 사라진다."""
    st = parse_state_file(_REAL_SHAPE)
    assert len(st.stages) == 6, [s.name for s in st.stages]


def test_current_stage_still_resolves_with_a_phase_prefix():
    """`DISCOVERY - Envision`처럼 접두사가 붙어도 부분 포함 폴백이 자기 줄을
    찾아야 한다 — 못 찾으면 진행 중 스테이지가 아무것도 아니게 된다."""
    st = parse_state_file(_REAL_SHAPE)
    assert [s.name for s in st.stages if s.status == "in_progress"] == ["Envision"]


def test_falls_back_to_the_whole_document_without_the_section():
    """섹션이 없는 문서는 옛 동작으로 훑는다. 빈 사이드바는 잘못된 항목이 섞이는
    것보다 나쁘다 — 질문 파싱이 조용히 실패해 질문이 사라졌던 것과 같은 종류다."""
    st = parse_state_file(
        "# AI-PLC State\n"
        "- **Current Stage**: Envision\n"
        "- [x] Workspace Detection\n"
        "- [ ] Envision\n"
    )
    assert [s.name for s in st.stages] == ["Workspace Detection", "Envision"]
    assert [s.status for s in st.stages] == ["completed", "in_progress"]


def test_a_section_that_is_empty_yields_no_stages():
    """상류 템플릿의 초기 상태다 — `[Will be populated as workflow progresses]`.
    폴백이 여기서 켜지면 문서 나머지의 체크박스를 스테이지로 읽는다."""
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
