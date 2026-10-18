import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StageTimeline } from "./StageTimeline";
import { projectState } from "@/test/fixtures/projectState";

describe("StageTimeline", () => {
  it("renders every capability name from the backend state (nothing hardcoded)", () => {
    render(<StageTimeline state={projectState} projectId="ws-m" />);
    for (const c of projectState.capabilities) {
      expect(screen.getByText(c.name)).toBeInTheDocument();
    }
  });

  it("shows a 진행 중 pill and a wizard link for the in_progress stage", () => {
    render(<StageTimeline state={projectState} projectId="ws-m" />);
    expect(screen.getByText("진행 중")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /질문 답변 계속하기/ });
    expect(link).toHaveAttribute("href", "/projects/ws-m/questions");
  });

  it("단계가 하나도 없으면 빈 상태 문구를 보여준다", () => {
    // A freshly created project's `stages` is an empty array, and if only an empty <ol> remained then, it
    // would become a big white box that reads as broken. The sibling panels (ArtifactsPanel, ActivityFeed)
    // all have empty-state text.
    render(
      <StageTimeline
        state={{ project_type: null, current_stage: null, stages: [], capabilities: [] }}
        projectId="test333"
      />,
    );
    expect(screen.getByText("아직 실행된 단계가 없습니다.")).toBeInTheDocument();
  });

  it("marks completed capabilities with 완료", () => {
    render(<StageTimeline state={projectState} projectId="ws-m" />);
    expect(screen.getAllByText("완료").length).toBe(
      projectState.capabilities.filter((c) => c.status === "completed").length,
    );
  });

  // Shows the official 6, not the stage list the agent wrote (8).
  it("lists the six official capabilities, marking the ones this path skips", () => {
    render(<StageTimeline state={projectState} projectId="ws-m" />);
    expect(screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual([
      "Envision", "Use Case Intake", "Prioritize", "Prototype", "Product Strategy", "Go-to-Market",
    ]);
    expect(screen.queryByText("Workspace Detection")).toBeNull();
    expect(screen.getAllByText("이 경로에 없음")).toHaveLength(2);
  });
});
