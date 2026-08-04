# backend/tests/test_project_model.py
#
# 모델 해석의 폴백 순서와, 그것이 실제로 세 주입 지점에 닿는지.
from __future__ import annotations

import pytest

import pathfinder.app as app_module


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
    # 로컬 개발: 둘 다 없으면 None — 드라이버가 env를 넣지 않아 SDK 기본값으로
    # 간다(종전 동작).
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    app_module.registry.register("pm-test", None)
    assert app_module.project_model("pm-test") is None


def test_project_model_for_an_unregistered_project_uses_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "global.anthropic.claude-opus-4-8")
    assert app_module.project_model("never-registered") == "global.anthropic.claude-opus-4-8"


def test_driver_factory_passes_the_projects_model(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_MODEL", "global.anthropic.claude-opus-4-8")
    monkeypatch.setenv("PATHFINDER_DISCOVERY_DRIVER", "claude")
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: object())
    app_module.registry.register("pm-test", None,
                                 model_id="global.anthropic.claude-sonnet-5")
    driver = app_module.driver_factory("pm-test", tmp_path)
    assert driver._anthropic_model == "global.anthropic.claude-sonnet-5"


def test_builder_factory_passes_the_projects_model(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_MODEL", "global.anthropic.claude-opus-4-8")
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    monkeypatch.setenv("PATHFINDER_PROTO_ROOT", str(tmp_path / "protos"))
    monkeypatch.setenv("PATHFINDER_PROTO_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: object())
    app_module.registry.register("pm-test", None,
                                 model_id="global.anthropic.claude-sonnet-5")
    session = app_module.proto_session_factory("pm-test", "slug")
    builder = session._builder_factory("sid", False)
    assert builder._anthropic_model == "global.anthropic.claude-sonnet-5"


@pytest.mark.asyncio
async def test_questionnaire_agent_factory_raises_when_no_model(monkeypatch):
    # 설문 생성은 여기가 유일하게 모델을 필수로 요구하는 지점이다. 없으면
    # 502로 번역될 RuntimeError를 내고 이유를 남긴다 — KeyError로 터지면
    # 로그에 'ANTHROPIC_MODEL'만 남고 프로젝트 설정 문제인지 알 수 없다.
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    app_module.registry.register("pm-test", None)
    call = app_module.questionnaire_agent_factory("pm-test")
    with pytest.raises(RuntimeError, match="no model"):
        await call("프롬프트")


def test_proto_session_factory_passes_the_projects_language(monkeypatch, tmp_path):
    """프로토타입 세션과 빌더가 같은 프로젝트 언어를 받는다.

    둘이 어긋나면 개시 프롬프트와 build_complete 도구 설명의 언어가 갈린다 —
    에러는 없고, 영어 대화 중에 한국어 도구 설명이 섞인다.
    """
    monkeypatch.delenv("PATHFINDER_S3_BUCKET", raising=False)
    monkeypatch.setenv("PATHFINDER_PROTO_ROOT", str(tmp_path / "protos"))
    monkeypatch.setenv("PATHFINDER_PROTO_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(app_module, "s3_store_factory", lambda pid: object())
    app_module.registry.register("pm-test", None, language="en")
    session = app_module.proto_session_factory("pm-test", "slug")
    assert session._language == "en"
    builder = session._builder_factory("sid", False)
    assert builder._language == "en"


def test_proto_tools_get_the_builders_language(tmp_path):
    """빌더 → 도구 배선. 이 홉이 끊기면 도구 설명과 반환 문자열이 항상
    한국어로 남는다(둘 다 모델이 읽는 프롬프트다)."""
    from pathfinder.proto.builder import PrototypeBuilder, _proto_tools_for
    builder = PrototypeBuilder(workspace=str(tmp_path), config_dir=str(tmp_path),
                               session_id="s", resume=False, language="en",
                               client_factory=lambda: None)
    tools = _proto_tools_for(builder)
    # SdkMcpTool의 설명이 영어여야 한다 — 한글이 섞이면 배선이 끊긴 것이다.
    described = " ".join(str(getattr(t, "description", "")) for t in tools)
    assert described.strip() != ""
    assert not any("가" <= c <= "힣" for c in described), described
