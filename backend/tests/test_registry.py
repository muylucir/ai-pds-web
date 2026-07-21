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
