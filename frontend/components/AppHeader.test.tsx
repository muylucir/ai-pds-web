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
    for (const label of ["대시보드", "질문 답변", "문서 리뷰", "빌드 캔버스"]) {
      expect(screen.queryByRole("link", { name: label })).toBeNull();
      expect(screen.getByText(label)).toHaveAttribute("aria-disabled", "true");
    }
  });
});

describe("AppHeader with a selected project", () => {
  it("renders the per-project tabs as links into that project's routes", () => {
    render(<AppHeader activeTab="dashboard" projectId="pilot1" />);
    expect(screen.getByRole("link", { name: "대시보드" })).toHaveAttribute(
      "href", "/projects/pilot1/dashboard",
    );
    expect(screen.getByRole("link", { name: "질문 답변" })).toHaveAttribute(
      "href", "/projects/pilot1/questions",
    );
    expect(screen.getByRole("link", { name: "문서 리뷰" })).toHaveAttribute(
      "href", "/projects/pilot1/review",
    );
  });

  it("marks the active tab with aria-current", () => {
    render(<AppHeader activeTab="questions" projectId="pilot1" />);
    expect(screen.getByRole("link", { name: "질문 답변" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "대시보드" })).not.toHaveAttribute("aria-current");
  });

  it("links the 빌드 캔버스 tab into the project's canvas route", () => {
    render(<AppHeader activeTab="canvas" projectId="pilot1" />);
    const link = screen.getByRole("link", { name: "빌드 캔버스" });
    expect(link).toHaveAttribute("href", "/projects/pilot1/canvas");
    expect(link).toHaveAttribute("aria-current", "page");
  });
});
