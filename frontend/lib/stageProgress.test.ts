import { describe, it, expect } from "vitest";
import { stageCounts, progressPercent, answeredCount } from "./stageProgress";
import { projectState } from "@/test/fixtures/projectState";
import { strategyQuestions } from "@/test/fixtures/strategyQuestions";
import type { ProjectState } from "@/lib/api/types";

describe("stageProgress helpers", () => {
  it("counts completed / total stages from the pilot fixture", () => {
    const { completed, total } = stageCounts(projectState);
    expect(total).toBe(8);
    expect(completed).toBe(5); // matches mockup 01: "5 / 8"
  });

  it("progressPercent rounds completed/total", () => {
    expect(progressPercent(projectState)).toBe(63); // round(5/8*100)
  });

  it("progressPercent is 0 for an empty state", () => {
    const empty: ProjectState = { project_type: null, current_stage: null, stages: [] };
    expect(progressPercent(empty)).toBe(0);
  });

  it("answeredCount counts non-empty answers", () => {
    const { answered, total } = answeredCount(strategyQuestions);
    expect(total).toBe(13);
    expect(answered).toBeGreaterThan(0);
  });
});
