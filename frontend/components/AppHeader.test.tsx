import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { AppHeader } from "./AppHeader";

describe("AppHeader", () => {
  it("renders the brand and the three Korean nav labels", () => {
    render(<AppHeader activeTab="dashboard" />);
    expect(screen.getByText("Pathfinder")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "대시보드" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "질문 답변" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "문서 리뷰" })).toBeInTheDocument();
  });

  it("marks the active tab with aria-current", () => {
    render(<AppHeader activeTab="questions" />);
    expect(screen.getByRole("link", { name: "질문 답변" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "대시보드" })).not.toHaveAttribute("aria-current");
  });
});

describe("AppHeader canvas tab", () => {
  it("links the 빌드 캔버스 tab into the project's canvas route", () => {
    render(<AppHeader activeTab="canvas" projectId="pilot1" />);
    const link = screen.getByRole("link", { name: "빌드 캔버스" });
    expect(link).toHaveAttribute("href", "/projects/pilot1/canvas");
    expect(link).toHaveAttribute("aria-current", "page");
  });
});
