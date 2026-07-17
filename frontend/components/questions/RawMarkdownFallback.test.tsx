import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RawMarkdownFallback } from "./RawMarkdownFallback";
import { unparsedQuestions } from "@/test/fixtures/unparsedQuestions";

describe("RawMarkdownFallback", () => {
  it("renders the parse-failure notice and the raw markdown", () => {
    render(<RawMarkdownFallback file={unparsedQuestions} onSubmit={vi.fn()} submitting={false} />);
    expect(screen.getByText(/표준 형식으로 파싱하지 못했습니다/)).toBeInTheDocument();
    expect(screen.getByText("자유 형식 메모")).toBeInTheDocument(); // rendered from raw_markdown heading
  });

  it("submits the free-text answer", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<RawMarkdownFallback file={unparsedQuestions} onSubmit={onSubmit} submitting={false} />);
    await user.type(screen.getByLabelText(/자유 답변/), "제 답변입니다");
    await user.click(screen.getByRole("button", { name: /제출/ }));
    expect(onSubmit).toHaveBeenCalledWith("제 답변입니다");
  });
});
