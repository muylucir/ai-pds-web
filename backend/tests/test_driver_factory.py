# env 토글 — 워크숍 중 문제가 나면 env 하나로 되돌린다. 다섯 번의 배포 사고를
# 겪은 만큼 탈출로를 둔다.
from pathlib import Path

import pytest

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
