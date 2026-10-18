// frontend/lib/protoAgents.test.ts
import { describe, expect, it } from "vitest";

import { applyAgentActivity, runningAgents, type AgentRow } from "./protoAgents";

const T0 = 1_700_000_000_000;

function open(id: string, label: string, at = T0): AgentRow[] {
  return applyAgentActivity([], { task_id: id, state: "started", label }, at);
}

describe("applyAgentActivity", () => {
  it("started가 자기 시작 시각을 가진 행을 연다", () => {
    // Each row must have its own elapsed time — parallel agents start at different moments.
    const rows = open("a040", "화면 골격");
    expect(rows).toEqual([
      {
        id: "a040",
        label: "화면 골격",
        tool: null,
        detail: null,
        startedAt: T0,
        status: null,
        summary: null,
      },
    ]);
  });

  it("에이전트마다 별개의 행을 유지한다", () => {
    let rows = open("a040", "화면 골격");
    rows = applyAgentActivity(rows, { task_id: "a187", state: "started", label: "데이터 모델" }, T0 + 500);
    expect(rows.map((r) => r.id)).toEqual(["a040", "a187"]);
    expect(rows.map((r) => r.startedAt)).toEqual([T0, T0 + 500]);
  });

  it("진행 갱신이 라벨을 지우지 않는다", () => {
    // started is the only source of label. Reading its absence as "clear it" would make a row lose its name
    // on the first heartbeat.
    let rows = open("a040", "화면 골격");
    rows = applyAgentActivity(rows, { task_id: "a040", state: "progress", tool: "Write" }, T0 + 1);
    expect(rows[0]!.label).toBe("화면 골격");
    expect(rows[0]!.tool).toBe("Write");
  });

  it("같은 도구가 계속 도는 동안 대상을 유지한다", () => {
    // Two paths report the same tool: ToolUseBlock carries the target,
    // TaskProgress carries only the tool name. If the latter cleared the target, the screen would flicker.
    let rows = open("a040", "화면 골격");
    rows = applyAgentActivity(
      rows, { task_id: "a040", state: "progress", tool: "Write", detail: "app/page.tsx" }, T0 + 1);
    rows = applyAgentActivity(rows, { task_id: "a040", state: "progress", tool: "Write" }, T0 + 2);
    expect(rows[0]!.detail).toBe("app/page.tsx");
  });

  it("도구가 바뀌면 이전 도구의 대상을 버린다", () => {
    // Applying the keep rule unconditionally would attach a file that was never read to the new tool,
    // like `Read · app/page.tsx`.
    let rows = open("a040", "화면 골격");
    rows = applyAgentActivity(
      rows, { task_id: "a040", state: "progress", tool: "Write", detail: "app/page.tsx" }, T0 + 1);
    rows = applyAgentActivity(rows, { task_id: "a040", state: "progress", tool: "Bash" }, T0 + 2);
    expect(rows[0]!).toMatchObject({ tool: "Bash", detail: null });
  });

  it("done이 행을 닫고 요약을 남긴다", () => {
    let rows = open("a040", "화면 골격");
    rows = applyAgentActivity(
      rows,
      { task_id: "a040", state: "done", status: "completed", summary: "app/page.tsx를 만들었다" },
      T0 + 3,
    );
    expect(rows[0]!).toMatchObject({ status: "completed", summary: "app/page.tsx를 만들었다" });
  });

  it("실패도 종료로 다룬다", () => {
    // Treating only completed as terminal would leave a failed agent's row spinning forever.
    let rows = open("a040", "화면 골격");
    rows = applyAgentActivity(rows, { task_id: "a040", state: "done", status: "failed" }, T0 + 3);
    expect(runningAgents(rows)).toEqual([]);
  });

  it("started를 못 본 갱신도 행을 연다", () => {
    // A row without a label is better than hiding an agent in progress.
    const rows = applyAgentActivity([], { task_id: "a040", state: "progress", tool: "Read" }, T0);
    expect(rows[0]!).toMatchObject({ id: "a040", label: null, tool: "Read", status: null });
  });

  it("task_id가 없는 갱신은 무시한다", () => {
    // Making rows with an empty key would lump different agents into one row.
    const rows = applyAgentActivity(open("a040", "x"), { task_id: "", state: "progress", tool: "Read" }, T0);
    expect(rows.map((r) => r.id)).toEqual(["a040"]);
  });

  it("입력 배열을 변형하지 않는다", () => {
    const rows = open("a040", "화면 골격");
    const before = structuredClone(rows);
    applyAgentActivity(rows, { task_id: "a040", state: "progress", tool: "Write" }, T0 + 1);
    expect(rows).toEqual(before);
  });
});

describe("runningAgents", () => {
  it("끝난 행은 고정 줄에서 빠진다", () => {
    let rows = open("a040", "화면 골격");
    rows = applyAgentActivity(rows, { task_id: "a187", state: "started", label: "데이터 모델" }, T0 + 1);
    rows = applyAgentActivity(rows, { task_id: "a040", state: "done", status: "completed" }, T0 + 2);
    expect(runningAgents(rows).map((r) => r.id)).toEqual(["a187"]);
  });
});
