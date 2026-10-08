# backend/tests/test_capabilities.py
from pathlib import Path

import pytest

from aipds.capabilities import CAPABILITIES, capability_of, rollup
from aipds.models import StageState
from aipds.parsers.state import parse_state_file

REAL = Path(__file__).parent / "fixtures" / "real"


def _st(name, status="pending", note=None):
    return StageState(name=name, status=status, note=note)


def _statuses(caps):
    return {c.key: c.status for c in caps}


@pytest.mark.parametrize("name,key", [
    ("Workspace Detection", None),
    ("Discovery Mode Selection (Path A 선택)", None),
    ("Discovery Mode Selection Extended Review", None),
    ("Envision (PR/FAQ)", "envision"),
    ("Solution Analysis (Ggepom solution — Path A.7)", "envision"),
    ("Use Case Intake", "use_case_intake"),
    ("Use Case Prioritization", "prioritize"),
    ("Prototype Context Generation", "prototype"),
    ("Prototype & Validation", "prototype"),
    ("Product Strategy", "product_strategy"),
    ("Go-to-Market", "go_to_market"),
    ("Go-to-Market Strategy", "go_to_market"),
    ("Discovery Document", "go_to_market"),
    ("R&D Review", None),
])
def test_stage_names_map_to_the_official_six(name, key):
    assert capability_of(name) == key


def test_always_six_in_the_official_order():
    assert [c.name for c in rollup([])] == [
        "Envision", "Use Case Intake", "Prioritize",
        "Prototype", "Product Strategy", "Go-to-Market"]
    assert [c.key for c in rollup([])] == [k for k, _ in CAPABILITIES]
    assert all(c.status == "pending" for c in rollup([]))


def test_a_capability_is_completed_only_when_all_its_stages_are():
    caps = _statuses(rollup([_st("Envision", "completed"),
                             _st("Solution Analysis")]))
    assert caps["envision"] == "in_progress"
    caps = _statuses(rollup([_st("Envision", "completed"),
                             _st("Solution Analysis", "completed")]))
    assert caps["envision"] == "completed"


def test_skipped_only_when_a_later_capability_is_listed():
    # Path A가 아직 Envision만 적었다 — 뒤의 것은 건너뛴 게 아니라 아직 안 적힌 것이다.
    caps = _statuses(rollup([_st("Envision", "in_progress")]))
    assert caps["use_case_intake"] == "pending"
    # Prototype이 적히면 그 앞의 Intake·Prioritize는 이 경로에 없다.
    caps = _statuses(rollup([_st("Envision", "completed"),
                             _st("Prototype & Validation", "in_progress")]))
    assert caps["use_case_intake"] == "not_applicable"
    assert caps["prioritize"] == "not_applicable"
    assert caps["product_strategy"] == "pending"


def test_path_b_skips_envision():
    caps = _statuses(rollup([_st("Use Case Intake", "completed"),
                             _st("Use Case Prioritization", "in_progress")]))
    assert caps["envision"] == "not_applicable"
    assert caps["prioritize"] == "in_progress"


def test_note_comes_from_the_active_stage():
    caps = rollup([_st("Envision", "completed", "PR/FAQ 승인"),
                   _st("Solution Analysis", "in_progress", "해법 2개 비교 중")])
    assert caps[0].note == "해법 2개 비교 중"


def test_real_projects():
    """실측 상태 파일 — 스테이지 목록 길이 5·6·11이 모두 같은 6개로 읽힌다."""
    def caps(project):
        st = parse_state_file((REAL / project / "aiplc-state.md").read_text(encoding="utf-8"))
        return _statuses(st.capabilities)

    done_a = {"envision": "completed", "use_case_intake": "not_applicable",
              "prioritize": "not_applicable", "prototype": "completed",
              "product_strategy": "completed", "go_to_market": "completed"}
    assert caps("ko-2") == done_a      # 5개 스테이지, 전부 완료
    assert caps("ko-3") == done_a
    # 11개 스테이지(Path A.2 — 여러 해법이라 Intake·Prioritize를 거친다)
    assert caps("ko-1") == {"envision": "in_progress", "use_case_intake": "pending",
                            "prioritize": "pending", "prototype": "pending",
                            "product_strategy": "pending", "go_to_market": "pending"}
    assert caps("ko-4")["use_case_intake"] == "not_applicable"
    assert caps("en-1")["product_strategy"] == "in_progress"


def test_project_state_serializes_capabilities():
    body = parse_state_file("## Stage Progress\n- [x] Envision\n").model_dump()
    assert [c["key"] for c in body["capabilities"]][0] == "envision"
    assert body["capabilities"][0]["status"] == "completed"
