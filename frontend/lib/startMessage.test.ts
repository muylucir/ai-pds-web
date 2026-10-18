import { describe, it, expect } from "vitest";
import { startMessage } from "./startMessage";

describe("startMessage", () => {
  it("프로젝트 언어의 개시 문장을 준다", () => {
    expect(startMessage("A", "ko")).toContain("Path A");
    expect(startMessage("A", "ko")).toContain("페인 포인트");
    expect(startMessage("A", "en")).toContain("pain points");
    expect(startMessage("B", "ko")).toContain("유스케이스");
    expect(startMessage("B", "en")).toContain("use cases");
  });

  it("두 경로가 서로 다른 문장이다", () => {
    // If the sentences were the same, the agent could not tell which path it is on.
    for (const lang of ["ko", "en"] as const) {
      expect(startMessage("A", lang)).not.toBe(startMessage("B", lang));
    }
  });

  it("어느 조합도 비어 있지 않다", () => {
    for (const lang of ["ko", "en"] as const) {
      for (const path of ["A", "B"] as const) {
        expect(startMessage(path, lang).trim()).not.toBe("");
      }
    }
  });
});
