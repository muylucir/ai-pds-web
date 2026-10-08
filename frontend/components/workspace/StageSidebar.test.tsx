import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StageSidebar, latestCapabilities } from "./StageSidebar";
import { LocaleProvider } from "@/lib/i18n/provider";
import { projectState } from "@/test/fixtures/projectState";
import type { CapabilityState } from "@/lib/api/types";

const allDone: CapabilityState[] = projectState.capabilities.map((c) =>
  c.status === "not_applicable" ? c : { ...c, status: "completed", note: null },
);

describe("latestCapabilities", () => {
  it("uses the server snapshot when no live event carried one", () => {
    expect(latestCapabilities(projectState, [])).toBe(projectState.capabilities);
  });

  it("the newest event snapshot replaces the server's", () => {
    const events = [
      { stage: "Product Strategy", status: "completed" as const, summary: "", capabilities: projectState.capabilities },
      { stage: "Go-to-Market", status: "completed" as const, summary: "", capabilities: allDone },
    ];
    expect(latestCapabilities(projectState, events)).toBe(allDone);
  });

  it("skips events without a snapshot instead of blanking the list", () => {
    const events = [
      { stage: "Go-to-Market", status: "completed" as const, summary: "", capabilities: allDone },
      { stage: "Go-to-Market", status: "completed" as const, summary: "" },
    ];
    expect(latestCapabilities(projectState, events)).toBe(allDone);
  });

  it("is empty before anything has loaded", () => {
    expect(latestCapabilities(null, [])).toEqual([]);
  });
});

describe("StageSidebar", () => {
  // 에이전트가 쓴 스테이지는 8개지만 사이드바는 공식 6개를 보인다
  // (Rachna 피드백: PDS는 6개인데 화면의 숫자가 달랐다).
  it("renders the six official capabilities, not the agent's raw stage list", () => {
    render(<StageSidebar state={projectState} events={[]} />);
    expect(screen.getByLabelText("단계 진행 상황")).toBeInTheDocument();
    for (const c of projectState.capabilities) {
      expect(screen.getByText(c.name)).toBeInTheDocument();
    }
    expect(screen.queryByText("Workspace Detection")).toBeNull();
    expect(screen.queryByText("Discovery Mode Selection")).toBeNull();
    expect(screen.getAllByText("이 경로에 없음")).toHaveLength(2);
  });

  it("shows the in-progress capability's note", () => {
    render(<StageSidebar state={projectState} events={[]} />);
    expect(screen.getByText(/13개 질문 대기/)).toBeInTheDocument();
  });

  it("follows a live event's snapshot over the server's initial state", () => {
    render(
      <StageSidebar
        state={projectState}
        events={[{ stage: "Go-to-Market", status: "completed", summary: "", capabilities: allDone }]}
      />,
    );
    expect(screen.getByText(/4 \/ 4/)).toBeInTheDocument();
  });

  // 이 카운터는 딕셔너리를 거치지 않고 "스테이지"를 리터럴로 박고 있었다 —
  // 영어 UI에서 헤딩과 힌트는 영어인데 그 줄만 한국어로 남았다(2026-08-04의
  // 스크린샷에 "0 / 0 스테이지"로 찍혀 있다).
  it("renders the stage-count unit in the UI locale, not a hardcoded Korean literal", () => {
    render(
      <LocaleProvider locale="en">
        <StageSidebar state={projectState} events={[]} />
      </LocaleProvider>,
    );
    expect(screen.getByText(/2 \/ 4 capabilities/)).toBeInTheDocument();
    expect(screen.getAllByText("Not on this path")).toHaveLength(2);
    expect(screen.queryByText(/스테이지/)).toBeNull();
  });
});
