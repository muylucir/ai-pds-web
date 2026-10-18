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
    expect(screen.getByText(/미완성 굵/)).toBeInTheDocument(); // rendered without crashing
  });
});

// 2026-08-16: the `[Answer]: A` the backend inserted did not appear on screen at all.
// That line is a CommonMark link reference definition, and the renderer produces no output for it.
describe("[Answer]: 줄", () => {
  it("기록된 답변이 화면에 나타난다", () => {
    // Whether this render path is alive is the entirety of this feature's visibility to the user.
    render(<Markdown text={"## Question 1\n질문?\n\n[Answer]: A\n"} />);
    expect(screen.getByText(/\[Answer\]:/)).toBeInTheDocument();
    expect(screen.getByText(/A$/)).toBeInTheDocument();
  });

  it("보기 안내 문구의 `[Answer]:`는 링크가 되지 않는다", () => {
    // If the definition remained, `[Answer]` in this text would turn into a link and the
    // brackets would vanish — that is exactly how the observed screen looked.
    const md = "X) Other (please describe after [Answer]: tag below)\n\n[Answer]: B\n";
    const { container } = render(<Markdown text={md} />);
    expect(container.querySelectorAll("a")).toHaveLength(0);
    expect(container.textContent).toContain("[Answer]: tag below");
  });
});

// The upstream format separates options only by line breaks. In CommonMark those line breaks
// render as spaces, so `A) … B) … C) …` ran together on one line and was unreadable.
describe("보기 줄", () => {
  it("보기가 각각 줄바꿈되어 렌더된다", () => {
    const md = "A) 연 1~2건\nB) 연 3~5건\nC) 연 6건 이상\n";
    const { container } = render(<Markdown text={md} />);
    // Hard breaks come out as <br> — with 3 options, 2 in between.
    expect(container.querySelectorAll("br").length).toBeGreaterThanOrEqual(2);
  });

  it("질문 본문은 줄바꿈이 강제되지 않는다", () => {
    // Sprinkling <br> through prose makes paragraphs ragged — which is why remark-breaks
    // is not used globally.
    const { container } = render(<Markdown text={"첫 줄입니다\n이어지는 산문입니다\n"} />);
    expect(container.querySelectorAll("br")).toHaveLength(0);
  });
});
