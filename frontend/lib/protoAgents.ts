// frontend/lib/protoAgents.ts — rows for the subagents running right now.
//
// **Why a list.** The pinned line above the input shows a single "most recent thing that
// happened" (liveActivity.ts) — exactly the shape that fails for parallel work. While three
// subagents run, that one line flickers among the three, and none of it reads as progress.
// That was exactly the impression users got ("it seems stuck"). So the parallel stretch needs
// several rows, and each row must carry **its own** elapsed time — the LiveActivityBar header's
// judgment that a rising number is the proof of liveness has to hold per row.
//
// This is a pure reducer (not a hook). That way the merge rule can be verified without a
// screen, and that rule contains the whole reason this file exists.
import type { AgentActivityPayload } from "@/lib/api/types";

export interface AgentRow {
  /** The row key (the backend's task_id). */
  id: string;
  /** The work that agent took on. The label from started — null if absent. */
  label: string | null;
  /** Name of the tool just run. The screen builds the label in the UI language. */
  tool: string | null;
  /** The tool's target (a file path, etc.). */
  detail: string | null;
  /** When this row opened (ms). Each row has a different elapsed time, so the row holds it. */
  startedAt: number;
  /** The result once finished. null while running. */
  status: string | null;
  /** The one-line note left on finishing. */
  summary: string | null;
}

/** Folds one `agent_activity` into the row list. Returns a new array (input unchanged).
 *
 *  Why `now` is an argument: calling `Date.now()` inside would make the tests that
 *  verify elapsed time depend on the clock.
 *
 *  ## Merge rule (the point of this function)
 *
 *  One agent's tool and target arrive by **two paths**:
 *
 *    - `TaskProgressMessage` → `tool` only (no target)
 *    - the subagent's `ToolUseBlock` → `tool` + `detail`
 *
 *  The observed order is not fixed (both have been seen). So overwriting `detail` every
 *  time makes the target flicker, and keeping it every time attaches the previous tool's
 *  target to the next tool. The rule is:
 *
 *    - `detail` arrives → use that value
 *    - no `detail` and `tool` **changed** → clear the target (it belonged to the previous tool)
 *    - no `detail` and `tool` unchanged → keep the previous target
 *
 *  Hence the contract that the backend omits None fields key and all — if they arrived as
 *  null they would hit the first rule and every heartbeat would clear the target.
 */
export function applyAgentActivity(
  rows: readonly AgentRow[],
  payload: AgentActivityPayload,
  now: number,
): AgentRow[] {
  const id = payload.task_id;
  // An update without task_id cannot be tied to any row — ignore it (making a row with an
  // empty key would lump different agents into one row).
  if (!id) return rows as AgentRow[];

  const index = rows.findIndex((r) => r.id === id);
  if (index === -1) {
    // An update whose `started` we never saw still opens a row. When frames lose order or the
    // user attaches mid-turn, a row without a label beats hiding an agent that is running.
    return [
      ...rows,
      {
        id,
        label: payload.label ?? null,
        tool: payload.tool ?? null,
        detail: payload.detail ?? null,
        startedAt: now,
        status: payload.state === "done" ? (payload.status ?? "completed") : null,
        summary: payload.summary ?? null,
      },
    ];
  }

  const prev = rows[index]!;
  const toolChanged = payload.tool !== undefined && payload.tool !== prev.tool;
  const next: AgentRow = {
    ...prev,
    // Once set, the label is kept: started is its only source, and later updates do not
    // carry it (if absence meant "clear", the row would lose its name on the first heartbeat).
    label: payload.label ?? prev.label,
    tool: payload.tool ?? prev.tool,
    detail: payload.detail ?? (toolChanged ? null : prev.detail),
    status: payload.state === "done" ? (payload.status ?? "completed") : prev.status,
    summary: payload.summary ?? prev.summary,
  };
  const out = [...rows];
  out[index] = next;
  return out;
}

/** Only the rows still running. This is what the pinned line draws. */
export function runningAgents(rows: readonly AgentRow[]): AgentRow[] {
  return rows.filter((r) => r.status === null);
}
