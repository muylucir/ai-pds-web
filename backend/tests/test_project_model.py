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
