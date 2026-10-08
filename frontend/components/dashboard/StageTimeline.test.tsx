import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StageTimeline } from "./StageTimeline";
import { projectState } from "@/test/fixtures/projectState";

describe("StageTimeline", () => {
  it("renders every capability name from the backend state (nothing hardcoded)", () => {
    render(<StageTimeline state={projectState} projectId="pilot1" />);
    for (const c of projectState.capabilities) {
      expect(screen.getByText(c.name)).toBeInTheDocument();
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
        state={{ project_type: null, current_stage: null, stages: [], capabilities: [] }}
        projectId="test333"
      />,
    );
    expect(screen.getByText("아직 실행된 단계가 없습니다.")).toBeInTheDocument();
  });

  it("marks completed capabilities with 완료", () => {
    render(<StageTimeline state={projectState} projectId="pilot1" />);
    expect(screen.getAllByText("완료").length).toBe(
      projectState.capabilities.filter((c) => c.status === "completed").length,
    );
  });

  // 에이전트가 쓴 스테이지 목록(8개)이 아니라 공식 6개를 보인다.
  it("lists the six official capabilities, marking the ones this path skips", () => {
    render(<StageTimeline state={projectState} projectId="pilot1" />);
    expect(screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual([
      "Envision", "Use Case Intake", "Prioritize", "Prototype", "Product Strategy", "Go-to-Market",
    ]);
    expect(screen.queryByText("Workspace Detection")).toBeNull();
    expect(screen.getAllByText("이 경로에 없음")).toHaveLength(2);
  });
});
