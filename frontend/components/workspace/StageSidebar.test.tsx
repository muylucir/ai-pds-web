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
  // The agent wrote 8 stages, but the sidebar shows the official 6
  // (AI-PDS team feedback: PDS has 6, but the number on screen was different).
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

  // This counter hardcoded "스테이지" (stages) as a literal without going through the dictionary —
  // in the English UI the heading and hint were English but that one line stayed Korean (the
  // 2026-08-04 screenshot shows "0 / 0 스테이지").
  it("renders the stage-count unit in the UI locale, not a hardcoded Korean literal", () => {
    render(
      <LocaleProvider locale="en">
        <StageSidebar state={projectState} events={[]} />
      </LocaleProvider>,
    );
    expect(screen.getByText(/2 \/ 4 phases/)).toBeInTheDocument();
    expect(screen.getAllByText("Not on this path")).toHaveLength(2);
    expect(screen.queryByText(/스테이지/)).toBeNull();
  });
});
