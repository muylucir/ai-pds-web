# env 토글 — 워크숍 중 문제가 나면 env 하나로 되돌린다. 다섯 번의 배포 사고를
# 겪은 만큼 탈출로를 둔다.
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pathfinder import app as app_module
from pathfinder.agent.claude_driver import ClaudeDriver
from pathfinder.agent.driver import StrandsDriver


def test_defaults_to_the_claude_driver(monkeypatch, tmp_path):
    monkeypatch.delenv("PATHFINDER_DISCOVERY_DRIVER", raising=False)
    d = app_module.driver_factory("p1", tmp_path)
    assert isinstance(d, ClaudeDriver)


def test_strands_opts_back_to_the_old_driver(monkeypatch, tmp_path):
    monkeypatch.setenv("PATHFINDER_DISCOVERY_DRIVER", "strands")
    d = app_module.driver_factory("p1", tmp_path)
    assert isinstance(d, StrandsDriver)


def test_an_unknown_value_is_a_deploy_accident(monkeypatch, tmp_path):
    # 오타가 조용히 기본값으로 떨어지면 어느 드라이버가 도는지 알 수 없다.
    monkeypatch.setenv("PATHFINDER_DISCOVERY_DRIVER", "claud")
    with pytest.raises(ValueError):
        app_module.driver_factory("p1", tmp_path)


def test_an_unknown_value_fails_at_startup_not_on_the_first_request(monkeypatch):
    # 리뷰에서 발견: driver_factory 안의 검증만으로는 오타가 기동 헬스체크를
    # 통과시키고(로드밸런서/배포 스크립트가 GET / 또는 /openapi.json에서 200을
    # 본다) 첫 워크숍 참가자가 프로젝트를 열 때(driver_factory 호출)서야
    # 죈다 — 다섯 번의 배포 사고를 겪은 이 프로젝트가 정확히 피하려는 모양.
    # TestClient의 with-구문이 lifespan을 실행하므로, 여기서 예외가 나야
    # "기동 시" ValueError라는 브리프의 요구가 실제로 성립한다.
    monkeypatch.setenv("PATHFINDER_DISCOVERY_DRIVER", "claud")
    with pytest.raises(ValueError):
        with TestClient(app_module.app):
            pass  # 여기까지 오면 기동이 성공했다는 뜻 — 검증이 늦다


def test_a_known_value_starts_up_fine(monkeypatch):
    # 대조: 유효한 값(과 미설정)은 기동을 막지 않는다.
    monkeypatch.delenv("PATHFINDER_DISCOVERY_DRIVER", raising=False)
    with TestClient(app_module.app) as client:
        assert client.get("/projects").status_code == 200
