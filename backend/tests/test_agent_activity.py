# backend/tests/test_agent_activity.py — the contract of the agent_activity payload.
#
# This payload is a **UI contract** (the only subagent representation the backend builds). If a field
# silently disappears, the screen's rows show up without labels or never close, so its shape is
# pinned here.
from __future__ import annotations

import json

from aipds.agent_activity import (SUMMARY_MAX, TERMINAL_TASK_STATUSES,
                                  activity_payload)


def test_started_carries_the_row_label():
    payload = json.loads(activity_payload(
        task_id="a040", state="started", label="화면 골격 만들기"))
    assert payload == {"task_id": "a040", "state": "started",
                       "label": "화면 골격 만들기"}


def test_none_fields_are_dropped_rather_than_sent_as_null():
    """A missing value and "the value is null" are handled differently in the frontend: the merge rule
    reads the **absence** of `detail` as "keep the previous value" (useProtoAgents). Sending null
    would make that rule clear the target on every heartbeat."""
    payload = json.loads(activity_payload(
        task_id="a040", state="progress", tool="Write"))
    assert payload == {"task_id": "a040", "state": "progress", "tool": "Write"}
    assert "detail" not in payload
    assert "label" not in payload


def test_a_long_summary_is_truncated():
    """Subagent summaries are observed to be hundreds of characters (probe: "Done.\\n\\n- Created …").
    The trace is a one-line list, so unless truncated it is unreadable — the same judgment as
    tool_trace's DETAIL_MAX."""
    payload = json.loads(activity_payload(
        task_id="a040", state="done", status="completed", summary="가" * 500))
    assert len(payload["summary"]) == SUMMARY_MAX + 1  # one character for the ellipsis
    assert payload["summary"].endswith("…")


def test_terminal_statuses_match_the_sdk():
    """If our list diverges from the SDK's, rows are left spinning forever
    (leaving out `killed` is the classic case — only task_updated sends that value).

    The reason for using our own constant rather than importing the SDK at runtime is that the translation layer does not
    depend on SDK types (that is why the fakes work). Instead, this test guards that the values match
    upstream."""
    from claude_agent_sdk import TERMINAL_TASK_STATUSES as upstream
    assert TERMINAL_TASK_STATUSES == upstream
