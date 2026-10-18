# backend/tests/test_cli_settings.py
#
# Context settings passed to the bundled CLI. Two things are guaranteed:
#
#   1. `[1m]` reaches **only the two factories that launch the CLI** and never
#      project_model. It is a CLI alias, not a Bedrock model id, so if it flows
#      into BedrockModel(model_id=...) on the questionnaire-generation path it
#      becomes a ValidationException — Discovery works fine while only
#      questionnaire generation silently breaks.
#   2. Both agents (Discovery and the prototype builder) receive the **same**
#      compaction window. If only one of them receives it, compaction timing
#      diverges within the same project, and that asymmetry shows up only as a
#      difference in artifact quality, with no error.
#   3. Both agents run on a CLI with background execution disabled. If it were
#      enabled, a turn would end before the work it started, and the remaining
#      progress would never reach the screen.
from __future__ import annotations

import pytest

import aipds.app as app_module
from aipds.cli_settings import (auto_compact_window, cli_context_env,
                                     cli_model_id, long_context_enabled)

_MODEL = "global.anthropic.claude-opus-5"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start with both switches explicitly off — if backend/.env is loaded
    (load_dotenv), the tests would vary with the deployment settings."""
    monkeypatch.delenv("AIPDS_LONG_CONTEXT", raising=False)
    monkeypatch.delenv("AIPDS_AUTO_COMPACT_WINDOW", raising=False)
    yield
    app_module.registry.remove("cs-test")


# ---- ① 1M suffix ----


def test_long_context_is_off_by_default():
    """The default must be off. Turning it on raises per-turn cost (if compaction
    is late, the entire history is resent every turn), and in very long contexts
    attention gets diluted, which can even make quality worse — it is a switch a
    deployment turns on after weighing the cost."""
    assert long_context_enabled() is False
    assert cli_model_id(_MODEL) == _MODEL


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_truthy_forms_turn_it_on(monkeypatch, value):
    monkeypatch.setenv("AIPDS_LONG_CONTEXT", value)
    assert cli_model_id(_MODEL) == f"{_MODEL}[1m]"


def test_a_none_model_stays_none(monkeypatch):
    """Appending the suffix to a missing value breaks the fallback — when the
    driver receives None, it does not set ANTHROPIC_MODEL and falls through to the
    CLI default (the last slot of project_model)."""
    monkeypatch.setenv("AIPDS_LONG_CONTEXT", "1")
    assert cli_model_id(None) is None


def test_the_suffix_is_not_doubled(monkeypatch):
    """Must be idempotent — on a deployment whose env default (ANTHROPIC_MODEL)
    already carries the suffix, emitting `...[1m][1m]` would leave the CLI unable
    to find the model."""
    monkeypatch.setenv("AIPDS_LONG_CONTEXT", "1")
    assert cli_model_id(f"{_MODEL}[1m]") == f"{_MODEL}[1m]"


# ---- ② Compaction window ----


def test_no_window_means_no_env_key():
    """When unset, the key is omitted entirely. If an empty string were set, the
    CLI would read it as a value."""
    assert auto_compact_window() is None
    assert "CLAUDE_CODE_AUTO_COMPACT_WINDOW" not in cli_context_env()


def test_a_valid_window_reaches_the_env(monkeypatch):
    monkeypatch.setenv("AIPDS_AUTO_COMPACT_WINDOW", "800000")
    assert cli_context_env()["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "800000"


@pytest.mark.parametrize("value", ["50", "2000000", "0", "-1"])
def test_a_window_outside_the_cli_range_is_dropped(monkeypatch, value):
    """The bundled CLI's settings schema accepts only 1e5..1e6. Passing an
    out-of-range value through makes the CLI reject the settings, and that
    rejection never shows up in our logs — dropping it here and logging a warning
    keeps it traceable."""
    monkeypatch.setenv("AIPDS_AUTO_COMPACT_WINDOW", value)
    assert "CLAUDE_CODE_AUTO_COMPACT_WINDOW" not in cli_context_env()


def test_a_non_numeric_window_is_dropped(monkeypatch):
    monkeypatch.setenv("AIPDS_AUTO_COMPACT_WINDOW", "8oo000")
    assert "CLAUDE_CODE_AUTO_COMPACT_WINDOW" not in cli_context_env()


# ---- ③ Wiring: project_model stays clean ----


def test_project_model_never_carries_the_cli_suffix(monkeypatch):
    """**The core invariant of this file.**

    project_model also flows into BedrockModel(model_id=...) on the
    questionnaire-generation path (app.questionnaire_agent_factory). If brackets
    get in there, Bedrock throws a ValidationException, and the symptom is
    "Discovery works but only questionnaire generation returns 502".
    """
    monkeypatch.setenv("AIPDS_LONG_CONTEXT", "1")
    app_module.registry.register("cs-test", None, model_id=_MODEL)
    assert app_module.project_model("cs-test") == _MODEL


def test_driver_factory_applies_the_suffix(monkeypatch, tmp_path):
    monkeypatch.setenv("AIPDS_LONG_CONTEXT", "1")
    monkeypatch.setenv("AIPDS_DISCOVERY_DRIVER", "claude")
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: object())
    app_module.registry.register("cs-test", None, model_id=_MODEL)
    driver = app_module.driver_factory("cs-test", tmp_path)
    assert driver._anthropic_model == f"{_MODEL}[1m]"


def test_builder_factory_applies_the_suffix(monkeypatch, tmp_path):
    """The builder must receive the same value — the sessions in which prototype
    builds were measured hitting compaction were on this side (264,040 → 53,375
    tokens)."""
    monkeypatch.setenv("AIPDS_LONG_CONTEXT", "1")
    monkeypatch.delenv("AIPDS_S3_BUCKET", raising=False)
    monkeypatch.setenv("AIPDS_PROTO_ROOT", str(tmp_path / "protos"))
    monkeypatch.setenv("AIPDS_PROTO_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: object())
    app_module.registry.register("cs-test", None, model_id=_MODEL)
    session = app_module.proto_session_factory("cs-test", "slug")
    builder = session._builder_factory("sid", False)
    assert builder._anthropic_model == f"{_MODEL}[1m]"


# ---- ④ Wiring: the window is actually set in both CLI envs ----
#
# Other tests that inject client_factory do not take this path, so they all pass
# even if the wiring is missing (same reason as the comment on
# test_claude_driver._captured_options).


def _discovery_env(tmp_path, monkeypatch) -> dict:
    from aipds.agent.claude_driver import ClaudeDriver, _default_client_factory
    from tests.fakes.in_memory_s3 import FakeS3Store

    captured = {}

    class FakeClient:
        def __init__(self, options=None):
            captured["options"] = options

    import claude_agent_sdk
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", FakeClient)
    driver = ClaudeDriver(workspace=str(tmp_path), rules_dir=str(tmp_path),
                          config_dir=str(tmp_path / "cfg"), s3=FakeS3Store())
    _default_client_factory(driver)({"session_id": "p1", "resume": False})
    return captured["options"].env


def _builder_env(tmp_path, monkeypatch) -> dict:
    from aipds.proto.builder import PrototypeBuilder, _default_client_factory

    captured = {}

    class FakeClient:
        def __init__(self, options=None):
            captured["options"] = options

    import claude_agent_sdk
    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", FakeClient)
    b = PrototypeBuilder(
        workspace=str(tmp_path), config_dir=str(tmp_path / "config"),
        session_id="11111111-2222-3333-4444-555555555555", resume=False)
    _default_client_factory(b)()
    return captured["options"].env


def test_both_agents_get_the_same_compact_window(tmp_path, monkeypatch):
    monkeypatch.setenv("AIPDS_AUTO_COMPACT_WINDOW", "750000")
    discovery = _discovery_env(tmp_path / "d", monkeypatch)
    builder = _builder_env(tmp_path / "b", monkeypatch)
    assert discovery["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "750000"
    assert builder["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "750000"


def test_neither_agent_gets_the_key_when_unset(tmp_path, monkeypatch):
    discovery = _discovery_env(tmp_path / "d", monkeypatch)
    builder = _builder_env(tmp_path / "b", monkeypatch)
    assert "CLAUDE_CODE_AUTO_COMPACT_WINDOW" not in discovery
    assert "CLAUDE_CODE_AUTO_COMPACT_WINDOW" not in builder


# ---- ⑤ Background execution ----


def test_background_tasks_are_off_for_both_agents(tmp_path, monkeypatch):
    """The switch is a fixed value, not a setting — both screens follow a single
    turn and close the stream at `done`, so on any deployment, work that outlives
    the turn would never be visible."""
    discovery = _discovery_env(tmp_path / "d", monkeypatch)
    builder = _builder_env(tmp_path / "b", monkeypatch)
    assert discovery["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
    assert builder["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
