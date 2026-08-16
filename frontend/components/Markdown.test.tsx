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

// 2026-08-16: 백엔드가 심은 `[Answer]: A`가 화면에 전혀 나타나지 않았다.
// 그 줄은 CommonMark 링크 참조 정의이고 렌더러는 출력을 만들지 않는다.
describe("[Answer]: 줄", () => {
  it("기록된 답변이 화면에 나타난다", () => {
    // 이 렌더 경로가 살아 있는지가 이 기능의 사용자 가시성 전부다.
    render(<Markdown text={"## Question 1\n질문?\n\n[Answer]: A\n"} />);
    expect(screen.getByText(/\[Answer\]:/)).toBeInTheDocument();
    expect(screen.getByText(/A$/)).toBeInTheDocument();
  });

  it("보기 안내 문구의 `[Answer]:`는 링크가 되지 않는다", () => {
    // 정의가 남아 있으면 이 문구의 `[Answer]`가 링크로 바뀌고 대괄호가 사라진다 —
    // 실측 화면에서 정확히 그 모양이었다.
    const md = "X) Other (please describe after [Answer]: tag below)\n\n[Answer]: B\n";
    const { container } = render(<Markdown text={md} />);
    expect(container.querySelectorAll("a")).toHaveLength(0);
    expect(container.textContent).toContain("[Answer]: tag below");
  });
});

// 상류 형식은 보기를 줄바꿈으로만 구분한다. CommonMark에서 그 줄바꿈은 공백으로
// 렌더되므로 `A) … B) … C) …`가 한 줄로 쭉 이어져 읽을 수 없었다.
describe("보기 줄", () => {
  it("보기가 각각 줄바꿈되어 렌더된다", () => {
    const md = "A) 연 1~2건\nB) 연 3~5건\nC) 연 6건 이상\n";
    const { container } = render(<Markdown text={md} />);
    // 하드 브레이크가 <br>로 나온다 — 보기 3개면 사이에 2개.
    expect(container.querySelectorAll("br").length).toBeGreaterThanOrEqual(2);
  });

  it("질문 본문은 줄바꿈이 강제되지 않는다", () => {
    // 산문에 <br>을 뿌리면 문단이 들쭉날쭉해진다 — remark-breaks를 전역으로
    // 쓰지 않은 이유다.
    const { container } = render(<Markdown text={"첫 줄입니다\n이어지는 산문입니다\n"} />);
    expect(container.querySelectorAll("br")).toHaveLength(0);
  });
});
