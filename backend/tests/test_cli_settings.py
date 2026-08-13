# backend/tests/test_cli_settings.py
#
# 번들 CLI에 넘기는 컨텍스트 설정. 두 가지를 지킨다:
#
#   1. `[1m]`은 **CLI를 띄우는 두 팩토리에만** 닿고 project_model에는 닿지
#      않는다. 그것은 Bedrock 모델 id가 아니라 CLI 별칭이므로, 설문 생성 경로의
#      BedrockModel(model_id=...)에 흘러가면 ValidationException이 된다 —
#      Discovery는 정상인데 설문 생성만 조용히 깨지는 모양이다.
#   2. 두 에이전트(Discovery, 프로토타입 빌더)가 **같은** 컴팩션 윈도우를 받는다.
#      한쪽만 받으면 같은 프로젝트에서 컴팩션 시점이 갈리고, 그 비대칭은 에러
#      없이 산출물 품질 차이로만 나타난다.
from __future__ import annotations

import pytest

import pathfinder.app as app_module
from pathfinder.cli_settings import (auto_compact_window, cli_context_env,
                                     cli_model_id, long_context_enabled)

_MODEL = "global.anthropic.claude-opus-5"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """두 스위치를 명시적으로 끈 상태에서 시작한다 — backend/.env가 실려 있으면
    (load_dotenv) 테스트가 배포 설정에 따라 달라진다."""
    monkeypatch.delenv("PATHFINDER_LONG_CONTEXT", raising=False)
    monkeypatch.delenv("PATHFINDER_AUTO_COMPACT_WINDOW", raising=False)
    yield
    app_module.registry.remove("cs-test")


# ---- ① 1M 접미사 ----


def test_long_context_is_off_by_default():
    """기본이 꺼짐이어야 한다. 켜는 것은 턴당 비용이 늘고(컴팩션이 늦으면 전체
    이력이 매 턴 재전송된다) 초장문에서 주의가 희석돼 품질이 되레 나빠질 수도
    있다 — 배포가 비용을 보고 켜는 스위치다."""
    assert long_context_enabled() is False
    assert cli_model_id(_MODEL) == _MODEL


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_truthy_forms_turn_it_on(monkeypatch, value):
    monkeypatch.setenv("PATHFINDER_LONG_CONTEXT", value)
    assert cli_model_id(_MODEL) == f"{_MODEL}[1m]"


def test_a_none_model_stays_none(monkeypatch):
    """없는 값에 접미사를 붙이면 폴백이 깨진다 — 드라이버는 None을 받으면
    ANTHROPIC_MODEL을 넣지 않고 CLI 기본값으로 간다(project_model의 마지막 칸)."""
    monkeypatch.setenv("PATHFINDER_LONG_CONTEXT", "1")
    assert cli_model_id(None) is None


def test_the_suffix_is_not_doubled(monkeypatch):
    """멱등이어야 한다 — env 기본값(ANTHROPIC_MODEL)에 이미 접미사가 박혀 있는
    배포에서 `...[1m][1m]`이 나가면 CLI가 모델을 찾지 못한다."""
    monkeypatch.setenv("PATHFINDER_LONG_CONTEXT", "1")
    assert cli_model_id(f"{_MODEL}[1m]") == f"{_MODEL}[1m]"


# ---- ② 컴팩션 윈도우 ----


def test_no_window_means_no_env_key():
    """미설정이면 키를 아예 넣지 않는다. 빈 문자열을 넣으면 CLI가 그것을 값으로
    읽는다."""
    assert auto_compact_window() is None
    assert cli_context_env() == {}


def test_a_valid_window_reaches_the_env(monkeypatch):
    monkeypatch.setenv("PATHFINDER_AUTO_COMPACT_WINDOW", "800000")
    assert cli_context_env() == {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "800000"}


@pytest.mark.parametrize("value", ["50", "2000000", "0", "-1"])
def test_a_window_outside_the_cli_range_is_dropped(monkeypatch, value):
    """번들 CLI의 설정 스키마는 1e5..1e6만 받는다. 밖의 값을 그대로 넘기면 CLI가
    설정을 거부하는데 그 거부는 우리 로그에 남지 않는다 — 여기서 떨어뜨리고
    경고를 남기는 편이 추적 가능하다."""
    monkeypatch.setenv("PATHFINDER_AUTO_COMPACT_WINDOW", value)
    assert cli_context_env() == {}


def test_a_non_numeric_window_is_dropped(monkeypatch):
    monkeypatch.setenv("PATHFINDER_AUTO_COMPACT_WINDOW", "8oo000")
    assert cli_context_env() == {}


# ---- ③ 배선: project_model은 깨끗하게 남는다 ----


def test_project_model_never_carries_the_cli_suffix(monkeypatch):
    """**이 파일의 핵심 불변식.**

    project_model은 설문 생성 경로에서 BedrockModel(model_id=...)로도 흐른다
    (app.questionnaire_agent_factory). 거기에 대괄호가 들어가면 Bedrock이
    ValidationException을 던지고, 증상은 "Discovery는 되는데 설문 생성만 502"다.
    """
    monkeypatch.setenv("PATHFINDER_LONG_CONTEXT", "1")
    app_module.registry.register("cs-test", None, model_id=_MODEL)
    assert app_module.project_model("cs-test") == _MODEL


def test_driver_factory_applies_the_suffix(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHFINDER_LONG_CONTEXT", "1")
    monkeypatch.setenv("PATHFINDER_DISCOVERY_DRIVER", "claude")
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: object())
    app_module.registry.register("cs-test", None, model_id=_MODEL)
    driver = app_module.driver_factory("cs-test", tmp_path)
    assert driver._anthropic_model == f"{_MODEL}[1m]"


def test_builder_factory_applies_the_suffix(monkeypatch, tmp_path):
    """빌더도 같은 값을 받아야 한다 — 프로토타입 빌드가 컴팩션에 걸리는 것을
    실측한 세션이 바로 이쪽이다(264,040 → 53,375 토큰)."""
    monkeypatch.setenv("PATHFINDER_LONG_CONTEXT", "1")
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    monkeypatch.setenv("PATHFINDER_PROTO_ROOT", str(tmp_path / "protos"))
    monkeypatch.setenv("PATHFINDER_PROTO_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: object())
    app_module.registry.register("cs-test", None, model_id=_MODEL)
    session = app_module.proto_session_factory("cs-test", "slug")
    builder = session._builder_factory("sid", False)
    assert builder._anthropic_model == f"{_MODEL}[1m]"


# ---- ④ 배선: 두 CLI env에 윈도우가 실제로 실린다 ----
#
# client_factory를 주입하는 다른 테스트들은 이 경로를 타지 않으므로, 배선이
# 빠져도 전부 통과한다(test_claude_driver._captured_options의 주석과 같은 이유).


def _discovery_env(tmp_path, monkeypatch) -> dict:
    from pathfinder.agent.claude_driver import ClaudeDriver, _default_client_factory
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
    from pathfinder.proto.builder import PrototypeBuilder, _default_client_factory

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
    monkeypatch.setenv("PATHFINDER_AUTO_COMPACT_WINDOW", "750000")
    discovery = _discovery_env(tmp_path / "d", monkeypatch)
    builder = _builder_env(tmp_path / "b", monkeypatch)
    assert discovery["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "750000"
    assert builder["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "750000"


def test_neither_agent_gets_the_key_when_unset(tmp_path, monkeypatch):
    discovery = _discovery_env(tmp_path / "d", monkeypatch)
    builder = _builder_env(tmp_path / "b", monkeypatch)
    assert "CLAUDE_CODE_AUTO_COMPACT_WINDOW" not in discovery
    assert "CLAUDE_CODE_AUTO_COMPACT_WINDOW" not in builder
