# backend/tests/test_registry.py
import pytest
from aipds.workspace import ProjectRegistry


class _FakeWorkspace:  # Registry stores workspaces only as opaque objects
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
    reg.register("p2")  # restored project state
    assert reg.list_ids() == ["p2"]
    assert reg.is_registered("p2") and not reg.has_workspace("p2")
    assert reg.get_name("p2") is None
    with pytest.raises(KeyError):
        reg.get("p2")  # no workspace yet


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
    assert reg.remove("p3") is None  # idempotent


def test_unknown_pid_raises_keyerror():
    reg = ProjectRegistry()
    with pytest.raises(KeyError):
        reg.get_name("nope")


def test_register_stores_and_returns_model_id():
    from aipds.workspace import ProjectRegistry
    r = ProjectRegistry()
    r.register("p1", "이름", created_at="2026-08-01T00:00:00+00:00",
               model_id="global.anthropic.claude-opus-5")
    assert r.get_model_id("p1") == "global.anthropic.claude-opus-5"


def test_get_model_id_is_none_for_a_project_registered_without_one():
    from aipds.workspace import ProjectRegistry
    r = ProjectRegistry()
    r.register("p1", "이름")
    assert r.get_model_id("p1") is None


def test_get_model_id_is_none_for_an_unknown_project():
    # get_name raises KeyError, but this one returns None: the caller (project_model)
    # uses it as the first link of a fallback chain, so "no model" is enough for unregistered too.
    from aipds.workspace import ProjectRegistry
    assert ProjectRegistry().get_model_id("nope") is None


def test_remove_drops_the_model_id():
    from aipds.workspace import ProjectRegistry
    r = ProjectRegistry()
    r.register("p1", None, model_id="global.anthropic.claude-opus-5")
    r.remove("p1")
    assert r.get_model_id("p1") is None


def test_register_stores_and_returns_language():
    from aipds.workspace import ProjectRegistry
    r = ProjectRegistry()
    r.register("p1", "이름", language="en")
    assert r.get_language("p1") == "en"


def test_get_language_is_ko_for_a_project_registered_without_one():
    # Projects restored from an old manifest were all created in Korean,
    # so reading None as ko matches the facts.
    from aipds.workspace import ProjectRegistry
    r = ProjectRegistry()
    r.register("p1", "이름")
    assert r.get_language("p1") == "ko"


def test_get_language_is_ko_for_an_unknown_project():
    # A different choice from get_model_id returning None: a language has no valid
    # "none" state (it has to be written in some language). The registry settles it so
    # callers do not repeat the fallback.
    from aipds.workspace import ProjectRegistry
    assert ProjectRegistry().get_language("nope") == "ko"


def test_get_language_falls_back_for_a_junk_value():
    # Even if a corrupted manifest carries an arbitrary string, place_rules must still be
    # able to decide which instruction block to attach.
    from aipds.workspace import ProjectRegistry
    r = ProjectRegistry()
    r.register("p1", None, language="klingon")
    assert r.get_language("p1") == "ko"


def test_remove_drops_the_language():
    from aipds.workspace import ProjectRegistry
    r = ProjectRegistry()
    r.register("p1", None, language="en")
    r.remove("p1")
    assert r.get_language("p1") == "ko"
