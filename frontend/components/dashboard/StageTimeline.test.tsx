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

  it("단계가 하나도 없으면 빈 상태 문구를 보여준다", () => {
    // 갓 만든 프로젝트의 `stages`는 빈 배열이고, 그때 빈 <ol>만 남으면 큰 흰
    // 박스가 되어 고장으로 읽힌다. 자매 패널(ArtifactsPanel·ActivityFeed)은
    // 모두 빈 상태 문구를 갖고 있다.
    render(
      <StageTimeline
        state={{ project_type: null, current_stage: null, stages: [] }}
        projectId="test333"
      />,
    );
    expect(screen.getByText("아직 실행된 단계가 없습니다.")).toBeInTheDocument();
  });

  it("marks completed stages with 완료", () => {
    render(<StageTimeline state={projectState} projectId="pilot1" />);
    expect(screen.getAllByText("완료").length).toBe(
      projectState.stages.filter((s) => s.status === "completed").length,
    );
  });
});
