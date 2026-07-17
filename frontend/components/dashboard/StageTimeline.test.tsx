import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StageTimeline } from "./StageTimeline";
import { projectState } from "@/test/fixtures/projectState";

describe("StageTimeline", () => {
  it("renders every stage name from the backend state (nothing hardcoded)", () => {
    render(<StageTimeline state={projectState} projectId="pilot1" />);
    for (const s of projectState.stages) {
      expect(screen.getByText(s.name)).toBeInTheDocument();
    }
  });

  it("shows a 진행 중 pill and a wizard link for the in_progress stage", () => {
    render(<StageTimeline state={projectState} projectId="pilot1" />);
    expect(screen.getByText("진행 중")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /질문 답변 계속하기/ });
    expect(link).toHaveAttribute("href", "/projects/pilot1/questions");
  });

  it("marks completed stages with 완료", () => {
    render(<StageTimeline state={projectState} projectId="pilot1" />);
    expect(screen.getAllByText("완료").length).toBe(
      projectState.stages.filter((s) => s.status === "completed").length,
    );
  });
});
