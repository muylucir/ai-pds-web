import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QuestionSummaryCard } from "./QuestionSummaryCard";
import { strategyQuestions } from "@/test/fixtures/strategyQuestions";

describe("QuestionSummaryCard", () => {
  it("renders the collapsed summary with Q-chips and the verbatim submitted note", () => {
    render(<QuestionSummaryCard file={strategyQuestions} />);
    expect(screen.getByText(/13개 답변 완료/)).toBeInTheDocument();
    expect(
      screen.getByText("제출됨 · audit.md Entry 3 · 변경하려면 채팅으로 요청하세요"),
    ).toBeInTheDocument();
    expect(screen.getByText("Q1:A")).toBeInTheDocument();
    expect(screen.getByText("Q11:C")).toBeInTheDocument();
    // Question text is hidden until expanded.
    expect(
      screen.queryByText("Q1. 이 제품을 시장(조직 내)에서 어떻게 포지셔닝하시겠습니까?"),
    ).not.toBeInTheDocument();
  });

  it("expands to show each question's text and answer on 펼치기 click", async () => {
    const user = userEvent.setup();
    render(<QuestionSummaryCard file={strategyQuestions} />);
    await user.click(screen.getByRole("button", { name: "펼치기" }));
    const q1Text = screen.getByText(
      "Q1. 이 제품을 시장(조직 내)에서 어떻게 포지셔닝하시겠습니까?",
    );
    expect(q1Text).toBeInTheDocument();
    // Q1's own list item shows its answer.
    const q1Item = q1Text.closest("li")!;
    expect(within(q1Item).getByText("답변: A")).toBeInTheDocument();
    // 11 of the fixture's 13 questions are answered "A" (only Q11: "C",
    // Q12: "A,B" differ) — assert the true count instead of an unscoped
    // getByText, which is ambiguous across this fixture's repeated answers.
    expect(screen.getAllByText("답변: A")).toHaveLength(11);
  });
});
