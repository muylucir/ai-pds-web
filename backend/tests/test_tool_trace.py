# backend/tests/test_tool_trace.py
#
# 추론 과정 아코디언에서 도구가 **무엇을 했는지** 보이게 하는 값.
#
# 왜 필요한가: Write는 별도 `file_changed` 이벤트가 `path`를 들고 오므로 화면에
# `📝 파일 변경: aiplc-docs/audit.md`로 뜨는데, Read/Bash는 `status` 이벤트에 도구
# 이름만 실려 `Read`, `Bash`만 뜬다. 무엇을 읽었는지·무슨 명령을 돌렸는지가 트레이스의
# 요점인데 그것이 빠져 있었다.
#
# **라이브와 히스토리가 같은 표현을 써야 하므로 이 모듈이 단일 소유자다.**
# session_history.py의 해당 분기에 "라이브의 status 이벤트(도구 이름)와 같은 표현"
# 이라는 주석이 붙어 있다 — 한쪽만 고치면 새로고침 전후로 화면이 달라진다.
from __future__ import annotations

import pytest

from aipds.tool_trace import DETAIL_MAX, tool_detail


@pytest.mark.parametrize("name,inp,expected", [
    ("Read", {"file_path": "/ws/aiplc-docs/audit.md"}, "aiplc-docs/audit.md"),
    ("Bash", {"command": "ls -la aiplc-docs/"}, "ls -la aiplc-docs/"),
    ("Glob", {"pattern": "aiplc-docs/**/*.md"}, "aiplc-docs/**/*.md"),
    ("Grep", {"pattern": "페인 포인트"}, "페인 포인트"),
    ("ToolSearch", {"query": "select:Read,Edit"}, "select:Read,Edit"),
])
def test_the_detail_that_matters_per_tool(name, inp, expected):
    assert tool_detail(name, inp) == expected


@pytest.mark.parametrize("name", ["Write", "Edit", "MultiEdit"])
def test_file_tools_have_no_detail(name):
    """이미 `file_changed` 이벤트가 경로를 들고 온다 — 두 줄로 보이면 중복이다."""
    assert tool_detail(name, {"file_path": "/ws/x.md"}) is None


@pytest.mark.parametrize("name", ["mcp__pathfinder__submit_document",
                                  "mcp__pathfinder_proto__build_complete"])
def test_mcp_tools_have_no_detail(name):
    """전용 이벤트(document/build_complete)가 이미 구조화된 값을 보낸다.

    `mcp__pathfinder__report_stage`가 이 목록에 있었다. 그 도구는 2026-08-18에
    PostToolUse 훅으로 옮겨 갔으므로 도구 이름으로 나타나지 않는다 —
    `stage` 이벤트는 이제 `aiplc-state.md` 쓰기에서 유도된다(agent/reconcile.py).
    """
    assert tool_detail(name, {"path": "x.md", "version": "v1"}) is None


def test_an_unknown_tool_has_no_detail():
    """모르는 도구에 인자를 아무렇게나 붙여 보여주지 않는다 — 무엇이 의미 있는
    인자인지 모르는 채로 첫 값을 찍으면 내부 식별자가 화면에 새어 나온다."""
    assert tool_detail("SomeNewTool", {"secret_id": "abc"}) is None


def test_a_long_command_is_truncated():
    """Bash 명령은 길이 제한이 없다. 아코디언 한 줄을 넘기면 트레이스가 읽히지
    않으므로 자르되, 잘렸다는 사실이 보여야 한다."""
    long = "echo " + "x" * 400
    out = tool_detail("Bash", {"command": long})
    assert out is not None
    assert len(out) <= DETAIL_MAX + 1  # 말줄임표 한 글자
    assert out.endswith("…")


def test_paths_are_workspace_relative_when_possible():
    """절대 경로를 그대로 찍으면 `/opt/aipds/workspaces/{pid}/`가 화면에 붙는다 —
    사용자에게 의미 없고, 프로젝트 id가 트레이스에 새어 나온다."""
    assert tool_detail("Read", {"file_path": "/opt/aipds/workspaces/p1/"
                                             "aiplc-docs/discovery/x.md"}) \
        == "aiplc-docs/discovery/x.md"
    # 워크스페이스 밖이면 손대지 않는다(그 자체가 신호다).
    assert tool_detail("Read", {"file_path": "/etc/hosts"}) == "/etc/hosts"


def test_missing_or_wrong_shaped_input_is_not_an_error():
    """도구 입력은 모델이 만든다 — 모양이 어긋나도 트레이스가 턴을 죽이면 안 된다."""
    assert tool_detail("Read", {}) is None
    assert tool_detail("Read", None) is None
    assert tool_detail("Bash", {"command": ""}) is None
    assert tool_detail("Bash", {"command": 123}) is None
