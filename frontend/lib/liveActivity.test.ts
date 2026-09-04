// frontend/lib/liveActivity.test.ts — 입력창 위 고정 줄이 무엇을 보여줄지 고르는 규칙.
//
// 고정 줄은 **가장 마지막에 일어난 일 하나만** 보여준다. 목록은 턴이 끝난 뒤
// 접힌 진행 기록이 갖는다. 그래서 이 함수가 고르는 것은 "지금"이지 "지금까지"가
// 아니다.
import { describe, it, expect } from "vitest";
import { liveActivity } from "./liveActivity";
import type { ChatItem } from "./useTurnStream";

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
    // 턴을 보낸 직후부터 첫 프레임까지의 짧은 구간. 빈 줄을 그리는 것보다
    // 사고로 두는 편이 정확하다 — 모델은 그때 거의 항상 생각하고 있다.
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
      { id: "c1", role: "card", card: "artifact", path: "x.md" },
    ];
    expect(liveActivity(items)).toEqual({ kind: "writing" });
  });
});
