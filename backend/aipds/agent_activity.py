# backend/aipds/agent_activity.py — "what is it doing right now" for one subagent.
#
# Once the agent started handing work to subagents, the build screen looked frozen for minutes.
# It was **not** because progress was not arriving — the SDK was already sending per-agent
# heartbeats as `Task*` messages (observed: claude_agent_sdk 0.2.143), but those four kinds are
# subclasses of `SystemMessage`, so they slipped straight through the translation layer, which uses
# `type(msg).__name__` equality. This module settles those heartbeats into one shape the screen reads.
#
# **Why a new event rather than `status`.** `status` is collapsed on screen into the single "most recent
# thing that happened" (liveActivity.ts) — exactly the shape that fails in parallel work.
# While three subagents run, that one line flickers among the three and none of them reads as
# progress. There need to be multiple rows, and for that the event must say **which** agent it
# belongs to. `task_id` is that value, and it is the entire reason this event exists separately.
#
# **Labels are not built here.** Same discipline as `tool_trace.py` — the backend provides only values
# (`tool: "Write"`), and the frontend draws `📝 파일 작성` in the UI language. There is one more particularly
# important reason: `TaskProgressMessage.description` is **English** prose made by the CLI
# ("Writing a.txt"), so it cannot be used as is in ko projects. So that field is dropped and
# only `last_tool_name` is carried — the frontend's ACTIVITY_LABEL_KEYS already translates tool names
# into the UI language.
from __future__ import annotations

import json

#: Event kind. Must be the same value as the Literal in `models.AgentEvent.kind`.
AGENT_ACTIVITY = "agent_activity"

#: Cap for the one-line summary. Subagent summaries are hundreds of characters in practice ("Done.\n\n- Created
#: `/…/a.txt` containing exactly `alpha`…"). The trace is a one-line list, so without truncation
#: it is unreadable — the same judgment as tool_trace.DETAIL_MAX; the value differs because a
#: summary is a sentence while detail is a path or command.
SUMMARY_MAX = 160

#: status values meaning the task has finished. **Covers both vocabularies** —
#: `task_notification` sends `stopped`, while `task_updated` sends the raw `killed`.
#:
#: Same values as the SDK's `TERMINAL_TASK_STATUSES`, and that equivalence is guarded by
#: tests/test_agent_activity.py. Upstream is not imported at runtime because the
#: translation layer does not depend on SDK types (which is why tests/fakes works).
TERMINAL_TASK_STATUSES: frozenset[str] = frozenset(
    {"completed", "failed", "stopped", "killed"}
)


def activity_payload(*, task_id: str, state: str, label: str | None = None,
                     tool: str | None = None, detail: str | None = None,
                     status: str | None = None,
                     summary: str | None = None) -> str:
    """One update to one agent row → SSE payload (JSON string).

    `task_id` is the row's key and `state` is what happened to that row:

      started   that agent came up. `label` is its assigned work (TaskStarted.description).
      progress  it is running `tool`. `detail` is the target (file path, etc.).
      done      it finished. `status` says how (completed/failed/stopped/killed),
                `summary` is the one line that agent left.

    **Fields that are None are dropped along with their keys.** Absence and null read differently on the frontend:
    the merge rule treats the absence of `detail` as "keep the previous value" (a mechanism that keeps
    heartbeats from clearing the target while the same tool keeps running), so sending null would make
    that rule clear the row's target every frame.
    """
    payload: dict[str, str] = {"task_id": task_id, "state": state}
    if label is not None:
        payload["label"] = label
    if tool is not None:
        payload["tool"] = tool
    if detail is not None:
        payload["detail"] = detail
    if status is not None:
        payload["status"] = status
    if summary is not None:
        text = summary.strip()
        if text:
            payload["summary"] = (text[:SUMMARY_MAX] + "…"
                                  if len(text) > SUMMARY_MAX else text)
    return json.dumps(payload, ensure_ascii=False)
