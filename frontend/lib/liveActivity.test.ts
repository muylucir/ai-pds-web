// frontend/lib/liveActivity.test.ts — rules for choosing what the pinned line above the input shows.
//
// The pinned line shows **only the single most recent thing that happened**. The list belongs to the
// progress log that collapses after the turn ends. So what this function picks is "now", not "so
// far".
import { describe, it, expect } from "vitest";
import { liveActivity } from "./liveActivity";
import type { ChatItem } from "./useWorkspaceStream";

function ai(over: Partial<Extract<ChatItem, { role: "ai" }>> = {}) {
  return {
    id: "a1", role: "ai" as const, text: "", trace: [], streaming: true,
    error: null, ...over,
  };
}

describe("liveActivity", () => {
  it("도는 턴이 없으면 null — 고정 줄을 그리지 않는다", () => {
    expect(liveActivity([])).toBeNull();
    expect(liveActivity([ai({ streaming: false })])).toBeNull();
  });

  it("도는 턴의 activity를 그대로 돌려준다", () => {
    const items = [ai({ activity: { kind: "tool", tool: "Read", detail: "a.md" } })];
    expect(liveActivity(items)).toEqual({ kind: "tool", tool: "Read", detail: "a.md" });
  });

  it("아직 프레임이 오지 않은 턴은 사고로 본다", () => {
    // The short interval from sending the turn until the first frame. Treating it as thinking is more
    // accurate than drawing an empty line — the model is almost always thinking at that point.
    expect(liveActivity([ai()])).toEqual({ kind: "thinking" });
  });

  it("여러 턴이 있으면 마지막 도는 턴을 고른다", () => {
    const items = [
      ai({ id: "a1", streaming: false, activity: { kind: "writing" } }),
      ai({ id: "a2", activity: { kind: "file", path: "b.md" } }),
    ];
    expect(liveActivity(items)).toEqual({ kind: "file", path: "b.md" });
  });

  it("사용자 말풍선과 카드는 건너뛴다", () => {
    const items: ChatItem[] = [
      { id: "u1", role: "user", text: "안녕" },
      ai({ activity: { kind: "writing" } }),
      { id: "c1", role: "history-card", name: "x" },
    ];
    expect(liveActivity(items)).toEqual({ kind: "writing" });
  });
});
