# backend/tests/test_tool_trace.py
#
# Values that show **what a tool did** in the reasoning accordion.
#
# Why it is needed: Write has a separate `file_changed` event carrying `path`, so it shows on screen as
# `📝 파일 변경: aiplc-docs/audit.md`, but Read/Bash carry only the tool name in the `status` event,
# so they show only as `Read`, `Bash`. What was read and which command was run are the point of the
# trace, and that was missing.
#
# **Live and history must use the same representation, so this module is the single owner.**
# The corresponding branch in session_history.py carries the comment "same representation as the
# live status event (tool name)" — fixing only one side makes the screen differ before and after a refresh.
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
    """The `file_changed` event already carries the path — showing it as two lines would be a duplicate."""
    assert tool_detail(name, {"file_path": "/ws/x.md"}) is None


@pytest.mark.parametrize("name", ["mcp__aipds_proto__build_complete"])
def test_mcp_tools_have_no_detail(name):
    """A dedicated event (build_complete) already sends structured values.

    `mcp__aipds__report_stage` and `mcp__aipds__submit_document` used to be on this list.
    Both moved to PostToolUse hooks (2026-08-18, 2026-08-21), so they no longer appear as
    tool names — `stage` is derived from writes to `aiplc-state.md`, and `document` from artifact writes
    (agent/reconcile.py). The list shrank to one, but parametrize is kept because when custom
    tools grow again they attach here as one line.
    """
    assert tool_detail(name, {"path": "x.md", "version": "v1"}) is None


def test_an_unknown_tool_has_no_detail():
    """Does not show arbitrary arguments for unknown tools — printing the first value without knowing
    which argument is meaningful would leak internal identifiers onto the screen."""
    # The key is named `secret_id` precisely because that is the shape of
    # argument that must not be echoed -- hence the nosec, not a rename.
    assert tool_detail("SomeNewTool", {"secret_id": "abc"}) is None  # nosec B105


def test_a_long_command_is_truncated():
    """Bash commands have no length limit. Beyond one accordion line the trace becomes unreadable,
    so they are truncated, but the fact that they were truncated must be visible."""
    long = "echo " + "x" * 400
    out = tool_detail("Bash", {"command": long})
    assert out is not None
    assert len(out) <= DETAIL_MAX + 1  # one ellipsis character
    assert out.endswith("…")


def test_paths_are_workspace_relative_when_possible():
    """Printing absolute paths as is would prefix `/opt/aipds/workspaces/{pid}/` on screen —
    meaningless to the user, and it leaks the project id into the trace."""
    assert tool_detail("Read", {"file_path": "/opt/aipds/workspaces/p1/"
                                             "aiplc-docs/discovery/x.md"}) \
        == "aiplc-docs/discovery/x.md"
    # Outside the workspace it is left untouched (that itself is a signal).
    assert tool_detail("Read", {"file_path": "/etc/hosts"}) == "/etc/hosts"


def test_missing_or_wrong_shaped_input_is_not_an_error():
    """Tool input is produced by the model — even if its shape is off, the trace must not kill the turn."""
    assert tool_detail("Read", {}) is None
    assert tool_detail("Read", None) is None
    assert tool_detail("Bash", {"command": ""}) is None
    assert tool_detail("Bash", {"command": 123}) is None
