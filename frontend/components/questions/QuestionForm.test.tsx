import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QuestionForm } from "./QuestionForm";
import { strategyQuestions } from "@/test/fixtures/strategyQuestions";

describe("QuestionForm", () => {
  it("renders the preamble and a progress count", () => {
    render(<QuestionForm file={strategyQuestions} onSubmit={vi.fn()} submitting={false} />);
    expect(screen.getByText(/추천 기본값/)).toBeInTheDocument();
    expect(screen.getByText(/13 답변/)).toBeInTheDocument();
  });

  it("submits an answers map keyed by question number", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<QuestionForm file={strategyQuestions} onSubmit={onSubmit} submitting={false} />);
    await user.click(screen.getByRole("button", { name: /답변 제출/ }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    const answers = onSubmit.mock.calls[0][0];
    // seeded from fixture answers: Q1 -> "A", Q12 -> "A,B"
    expect(answers["1"]).toBe("A");
    expect(answers["12"]).toBe("A,B");
  });

  it("열람 멤버에게는 제출 없이 잠긴 채로 보인다", () => {
    render(<QuestionForm file={strategyQuestions} onSubmit={vi.fn()} submitting={false} readOnly />);
    expect(screen.queryByRole("button", { name: /답변 제출/ })).toBeNull();
    expect(screen.getByText(/답은 프로젝트 소유자가 합니다/)).toBeInTheDocument();
    for (const input of screen.queryAllByRole("radio")) expect(input).toBeDisabled();
  });
});
