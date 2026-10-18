# backend/tests/test_project_model.py
#
# The fallback order of model resolution, and whether it actually reaches the three injection points.
from __future__ import annotations

import pytest

import aipds.app as app_module


@pytest.fixture(autouse=True)
def clean_registry():
    yield
    app_module.registry.remove("pm-test")


def test_project_model_prefers_the_projects_own_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "global.anthropic.claude-opus-4-8")
    app_module.registry.register("pm-test", None,
                                 model_id="global.anthropic.claude-opus-5")
    assert app_module.project_model("pm-test") == "global.anthropic.claude-opus-5"


def test_project_model_falls_back_to_env_when_project_has_none(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "global.anthropic.claude-opus-4-8")
    app_module.registry.register("pm-test", None)
    assert app_module.project_model("pm-test") == "global.anthropic.claude-opus-4-8"


def test_project_model_is_none_without_project_or_env(monkeypatch):
    # Local development: if neither is present, None — the driver does not set the env, so the SDK default
    # applies (the previous behavior).
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    app_module.registry.register("pm-test", None)
    assert app_module.project_model("pm-test") is None


def test_project_model_for_an_unregistered_project_uses_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "global.anthropic.claude-opus-4-8")
    assert app_module.project_model("never-registered") == "global.anthropic.claude-opus-4-8"


def test_driver_factory_passes_the_projects_model(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_MODEL", "global.anthropic.claude-opus-4-8")
    monkeypatch.setenv("AIPDS_DISCOVERY_DRIVER", "claude")
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: object())
    app_module.registry.register("pm-test", None,
                                 model_id="global.anthropic.claude-sonnet-5")
    driver = app_module.driver_factory("pm-test", tmp_path)
    assert driver._anthropic_model == "global.anthropic.claude-sonnet-5"


def test_builder_factory_passes_the_projects_model(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_MODEL", "global.anthropic.claude-opus-4-8")
    monkeypatch.delenv("AIPDS_S3_BUCKET", raising=False)
    monkeypatch.setenv("AIPDS_PROTO_ROOT", str(tmp_path / "protos"))
    monkeypatch.setenv("AIPDS_PROTO_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: object())
    app_module.registry.register("pm-test", None,
                                 model_id="global.anthropic.claude-sonnet-5")
    session = app_module.proto_session_factory("pm-test", "slug")
    builder = session._builder_factory("sid", False)
    assert builder._anthropic_model == "global.anthropic.claude-sonnet-5"


@pytest.mark.asyncio
async def test_questionnaire_agent_factory_raises_when_no_model(monkeypatch):
    # Survey generation is the only point that requires a model. Without one, it raises a
    # RuntimeError that will be translated into a 502 and records the reason — blowing up with a KeyError
    # would leave only 'ANTHROPIC_MODEL' in the log, with no way to tell it is a project settings problem.
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    app_module.registry.register("pm-test", None)
    call = app_module.questionnaire_agent_factory("pm-test")
    with pytest.raises(RuntimeError, match="no model"):
        await call("프롬프트")


def test_proto_session_factory_passes_the_projects_language(monkeypatch, tmp_path):
    """The prototype session and the builder receive the same project language.

    If the two diverge, the language of the opening prompt and of the build_complete tool description splits —
    there is no error, and Korean tool descriptions get mixed into an English conversation.
    """
    monkeypatch.delenv("AIPDS_S3_BUCKET", raising=False)
    monkeypatch.setenv("AIPDS_PROTO_ROOT", str(tmp_path / "protos"))
    monkeypatch.setenv("AIPDS_PROTO_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: object())
    app_module.registry.register("pm-test", None, language="en")
    session = app_module.proto_session_factory("pm-test", "slug")
    assert session._language == "en"
    builder = session._builder_factory("sid", False)
    assert builder._language == "en"


def test_proto_tools_get_the_builders_language(tmp_path):
    """Builder → tool wiring. If this hop breaks, the tool description and return strings always
    stay in Korean (both are prompts the model reads)."""
    from aipds.proto.builder import PrototypeBuilder, _proto_tools_for
    builder = PrototypeBuilder(workspace=str(tmp_path), config_dir=str(tmp_path),
                               session_id="s", resume=False, language="en",
                               client_factory=lambda: None)
    tools = _proto_tools_for(builder)
    # The SdkMcpTool's description must be in English — Hangul mixed in means the wiring is broken.
    described = " ".join(str(getattr(t, "description", "")) for t in tools)
    assert described.strip() != ""
    assert not any("가" <= c <= "힣" for c in described), described


def test_survey_store_factory_passes_the_projects_language(monkeypatch):
    """The survey store receives the project language — the report is an artifact that remains in
    aiplc-docs/**, so it follows the project language, not the UI language."""
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: object())
    monkeypatch.setattr(app_module, "surveys_root_s3_factory", lambda: object())
    app_module.registry.register("pm-test", None, language="en")
    store = app_module.survey_store_factory("pm-test", "slug")
    assert store._language == "en"
