# backend/tests/test_registry.py
import pytest
from pathfinder.workspace import ProjectRegistry


class _FakeWorkspace:  # Registry는 워크스페이스를 불투명 객체로만 저장한다
    pass


def test_register_then_attach_and_get():
    reg = ProjectRegistry()
    reg.register("p1", name="이름")
    ws = reg.attach("p1", _FakeWorkspace())
    assert reg.get("p1") is ws
    assert reg.is_registered("p1") and reg.has_workspace("p1")
    assert reg.get_name("p1") == "이름"


def test_register_only_is_listed_but_has_no_workspace():
    reg = ProjectRegistry()
    reg.register("p2")  # 복원된 프로젝트 상태
    assert reg.list_ids() == ["p2"]
    assert reg.is_registered("p2") and not reg.has_workspace("p2")
    assert reg.get_name("p2") is None
    with pytest.raises(KeyError):
        reg.get("p2")  # 워크스페이스는 아직 없음


def test_attach_without_register_raises():
    reg = ProjectRegistry()
    with pytest.raises(KeyError):
        reg.attach("ghost", _FakeWorkspace())


def test_remove_clears_both_and_returns_workspace():
    reg = ProjectRegistry()
    reg.register("p3")
    ws = reg.attach("p3", _FakeWorkspace())
    assert reg.remove("p3") is ws
    assert not reg.is_registered("p3") and not reg.has_workspace("p3")
    assert reg.remove("p3") is None  # 멱등


def test_unknown_pid_raises_keyerror():
    reg = ProjectRegistry()
    with pytest.raises(KeyError):
        reg.get_name("nope")


def test_register_stores_and_returns_model_id():
    from pathfinder.workspace import ProjectRegistry
    r = ProjectRegistry()
    r.register("p1", "이름", created_at="2026-08-01T00:00:00+00:00",
               model_id="global.anthropic.claude-opus-5")
    assert r.get_model_id("p1") == "global.anthropic.claude-opus-5"


def test_get_model_id_is_none_for_a_project_registered_without_one():
    from pathfinder.workspace import ProjectRegistry
    r = ProjectRegistry()
    r.register("p1", "이름")
    assert r.get_model_id("p1") is None


def test_get_model_id_is_none_for_an_unknown_project():
    # get_name은 KeyError를 내지만 이쪽은 None이다 — 호출부(project_model)가
    # 폴백 체인의 첫 칸으로 쓰므로 미등록도 "모델 없음"이면 충분하다.
    from pathfinder.workspace import ProjectRegistry
    assert ProjectRegistry().get_model_id("nope") is None


def test_remove_drops_the_model_id():
    from pathfinder.workspace import ProjectRegistry
    r = ProjectRegistry()
    r.register("p1", None, model_id="global.anthropic.claude-opus-5")
    r.remove("p1")
    assert r.get_model_id("p1") is None


def test_register_stores_and_returns_language():
    from pathfinder.workspace import ProjectRegistry
    r = ProjectRegistry()
    r.register("p1", "이름", language="en")
    assert r.get_language("p1") == "en"


def test_get_language_is_ko_for_a_project_registered_without_one():
    # 구 매니페스트로 복원된 프로젝트는 전부 한국어로 만들어진 것이므로,
    # None을 ko로 읽는 것이 사실에 맞다.
    from pathfinder.workspace import ProjectRegistry
    r = ProjectRegistry()
    r.register("p1", "이름")
    assert r.get_language("p1") == "ko"


def test_get_language_is_ko_for_an_unknown_project():
    # get_model_id가 None을 돌려주는 것과 다른 선택이다: 언어에는 "없음"이라는
    # 유효 상태가 없다(어떤 언어로든 써야 한다). 호출부가 폴백을 반복하지 않게
    # 레지스트리가 확정한다.
    from pathfinder.workspace import ProjectRegistry
    assert ProjectRegistry().get_language("nope") == "ko"


def test_get_language_falls_back_for_a_junk_value():
    # 손상된 매니페스트가 임의 문자열을 실어 와도 place_rules가 어느 지시
    # 블록을 붙일지 결정할 수 있어야 한다.
    from pathfinder.workspace import ProjectRegistry
    r = ProjectRegistry()
    r.register("p1", None, language="klingon")
    assert r.get_language("p1") == "ko"


def test_remove_drops_the_language():
    from pathfinder.workspace import ProjectRegistry
    r = ProjectRegistry()
    r.register("p1", None, language="en")
    r.remove("p1")
    assert r.get_language("p1") == "ko"
