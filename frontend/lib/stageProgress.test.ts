import { describe, it, expect } from "vitest";
import { stageCounts, progressPercent, answeredCount } from "./stageProgress";
import { projectState } from "@/test/fixtures/projectState";
import { strategyQuestions } from "@/test/fixtures/strategyQuestions";
import type { ProjectState } from "@/lib/api/types";

describe("stageProgress helpers", () => {
  it("counts completed / on-path capabilities, not the agent's raw stage list", () => {
    // 스테이지는 8개지만 capability로는 6개, 그중 Path A가 거치지 않는 2개는 분모에서 빠진다.
    const { completed, total } = stageCounts(projectState);
    expect(total).toBe(4);
    expect(completed).toBe(2);
  });

  it("progressPercent rounds completed/total", () => {
    expect(progressPercent(projectState)).toBe(50); // round(2/4*100)
  });

  it("progressPercent is 0 for an empty state", () => {
    const empty: ProjectState = { project_type: null, current_stage: null, stages: [], capabilities: [] };
    expect(progressPercent(empty)).toBe(0);
  });

  it("answeredCount counts non-empty answers", () => {
    const { answered, total } = answeredCount(strategyQuestions);
    expect(total).toBe(13);
    expect(answered).toBeGreaterThan(0);
  });
});
