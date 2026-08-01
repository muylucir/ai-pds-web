import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { AppHeader } from "./AppHeader";

describe("AppHeader without a selected project", () => {
  it("renders the brand and shows the per-project tabs DISABLED (not links)", () => {
    render(<AppHeader activeTab="projects" />);
    expect(screen.getByText("Pathfinder")).toBeInTheDocument();
    // The per-project tabs require a project, so with none selected they must
    // not be clickable links (a live link would navigate to a dead #/… route
    // and appear broken). They render as disabled spans instead.
    for (const label of ["대시보드", "워크스페이스", "문서 리뷰", "프로토타입"]) {
      expect(screen.queryByRole("link", { name: label })).toBeNull();
      expect(screen.getByText(label)).toHaveAttribute("aria-disabled", "true");
    }
  });

  it("no longer shows the retired 질문 답변 / 빌드 캔버스 tabs", () => {
    render(<AppHeader activeTab="projects" />);
    expect(screen.queryByText("질문 답변")).not.toBeInTheDocument();
    expect(screen.queryByText("빌드 캔버스")).not.toBeInTheDocument();
  });
});

describe("AppHeader with a selected project", () => {
  it("renders the per-project tabs as links into that project's routes", () => {
    render(<AppHeader activeTab="dashboard" projectId="pilot1" />);
    expect(screen.getByRole("link", { name: "대시보드" })).toHaveAttribute(
      "href", "/projects/pilot1/dashboard",
    );
    expect(screen.getByRole("link", { name: "워크스페이스" })).toHaveAttribute(
      "href", "/projects/pilot1/workspace",
    );
    expect(screen.getByRole("link", { name: "문서 리뷰" })).toHaveAttribute(
      "href", "/projects/pilot1/review",
    );
    expect(screen.getByRole("link", { name: "프로토타입" })).toHaveAttribute(
      "href", "/projects/pilot1/prototypes",
    );
  });

  it("marks the active tab with aria-current", () => {
    render(<AppHeader activeTab="workspace" projectId="pilot1" />);
    expect(screen.getByRole("link", { name: "워크스페이스" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "대시보드" })).not.toHaveAttribute("aria-current");
  });

  it("no longer shows the retired 질문 답변 / 빌드 캔버스 tabs", () => {
    render(<AppHeader activeTab="workspace" projectId="pilot1" />);
    expect(screen.queryByRole("link", { name: "질문 답변" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "빌드 캔버스" })).not.toBeInTheDocument();
  });
});

describe("AppHeader model badge", () => {
  it("shows the model badge when a label is given", () => {
    render(<AppHeader activeTab="workspace" projectId="p1" modelLabel="Opus 5" />);
    expect(screen.getByText("Opus 5")).toBeInTheDocument();
  });

  it("shows no model badge without a label", () => {
    render(<AppHeader activeTab="projects" />);
    expect(screen.queryByTestId("model-badge")).toBeNull();
  });
});
