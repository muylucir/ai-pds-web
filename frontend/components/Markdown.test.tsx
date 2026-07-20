import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Markdown } from "./Markdown";

describe("Markdown", () => {
  it("renders headings, bold, and GFM tables", () => {
    render(<Markdown text={"# 제목\n**굵게**\n\n| a | b |\n|---|---|\n| 1 | 2 |"} />);
    expect(screen.getByRole("heading", { name: "제목" })).toBeInTheDocument();
    expect(screen.getByText("굵게").tagName).toBe("STRONG");
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("does not render raw HTML (XSS)", () => {
    render(<Markdown text={'<img src=x onerror="alert(1)">텍스트'} />);
    expect(document.querySelector("img")).toBeNull();
  });

  it("opens links in a new tab with noopener", () => {
    render(<Markdown text={"[링크](https://example.com)"} />);
    const a = screen.getByRole("link", { name: "링크" });
    expect(a).toHaveAttribute("target", "_blank");
    expect(a.getAttribute("rel")).toContain("noopener");
  });

  it("renders incomplete markdown as plain text (streaming fallback)", () => {
    render(<Markdown text={"**미완성 굵"} />);
    expect(screen.getByText(/미완성 굵/)).toBeInTheDocument(); // 크래시 없이 표시
  });
});
