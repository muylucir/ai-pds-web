import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StageSidebar, mergeStages } from "./StageSidebar";
import { projectState } from "@/test/fixtures/projectState";
import type { StageState } from "@/lib/api/types";

describe("mergeStages", () => {
  it("returns the server stages unchanged when there are no events", () => {
    const server: StageState[] = [{ name: "Envision", status: "pending", note: null }];
    expect(mergeStages(server, [])).toEqual(server);
  });

  it("overrides an existing stage's status/note (summary→note) by stage-name match", () => {
    const server: StageState[] = [{ name: "Envision", status: "pending", note: "old note" }];
    const merged = mergeStages(server, [{ stage: "Envision", status: "in_progress", summary: "새 진행 상황" }]);
    expect(merged).toEqual([{ name: "Envision", status: "in_progress", note: "새 진행 상황" }]);
  });

  it("keeps the previous note when an event's summary is empty", () => {
    const server: StageState[] = [{ name: "Envision", status: "pending", note: "old note" }];
    const merged = mergeStages(server, [{ stage: "Envision", status: "in_progress", summary: "" }]);
    expect(merged[0].note).toBe("old note");
  });

  it("appends a stage the server didn't know about yet, defaulting from 'pending'", () => {
    const merged = mergeStages([], [{ stage: "New Stage", status: "in_progress", summary: "시작" }]);
    expect(merged).toEqual([{ name: "New Stage", status: "in_progress", note: "시작" }]);
  });

  it("the latest event for a stage wins when multiple events target the same stage", () => {
    const merged = mergeStages(
      [{ name: "Envision", status: "pending", note: null }],
      [
        { stage: "Envision", status: "in_progress", summary: "1차" },
        { stage: "Envision", status: "completed", summary: "2차" },
      ],
    );
    expect(merged).toEqual([{ name: "Envision", status: "completed", note: "2차" }]);
  });
});

describe("StageSidebar", () => {
  it("renders every merged stage name (server state, no events yet)", () => {
    render(<StageSidebar state={projectState} events={[]} />);
    expect(screen.getByLabelText("스테이지 진행 상황")).toBeInTheDocument();
    for (const s of projectState.stages) {
      expect(screen.getByText(s.name)).toBeInTheDocument();
    }
  });

  it("reflects a stage event's status override over the server's initial state", () => {
    render(
      <StageSidebar
        state={projectState}
        events={[{ stage: "Go-to-Market", status: "in_progress", summary: "마케팅 전략 초안" }]}
      />,
    );
    expect(screen.getByText("마케팅 전략 초안")).toBeInTheDocument();
  });
});
