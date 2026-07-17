import { describe, it, expect, vi } from "vitest";
import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QuestionCard } from "./QuestionCard";
import { strategyQuestions } from "@/test/fixtures/strategyQuestions";
import type { Question } from "@/lib/api/types";

const q1 = strategyQuestions.questions[0];

// Stateful harness: QuestionCard is a controlled component, so a bare vi.fn()
// onChange would leave `value` frozen and userEvent.type would report each
// keystroke against a non-advancing value (last call = a single char). This
// wrapper holds real state so `value` advances, letting us assert the final
// accumulated string while still spying on every onChange call.
function Harness({ question, initial, spy }: { question: Question; initial: string; spy: (v: string) => void }) {
  const [value, setValue] = useState(initial);
  return (
    <QuestionCard
      question={question}
      value={value}
      onChange={(v) => {
        spy(v);
        setValue(v);
      }}
    />
  );
}

describe("QuestionCard", () => {
  it("renders every option with letters and the ★ recommended pill", () => {
    render(<QuestionCard question={q1} value="A" onChange={vi.fn()} />);
    expect(screen.getByText(/Niche Specialist/)).toBeInTheDocument();
    expect(screen.getByText("★ AI 추천")).toBeInTheDocument(); // Q1 option A recommended
    expect(screen.getByText("X")).toBeInTheDocument(); // Other option chip
  });

  it("selecting an option reports its letter", async () => {
    const user = userEvent.setup();
    const spy = vi.fn();
    render(<Harness question={q1} initial="A" spy={spy} />);
    await user.click(screen.getByText(/플랫폼\(Platform\)/));
    expect(spy).toHaveBeenCalledWith("B");
  });

  it("typing in the Other textarea reports the free text", async () => {
    const user = userEvent.setup();
    const spy = vi.fn();
    render(<Harness question={q1} initial="" spy={spy} />);
    await user.type(screen.getByLabelText(/기타 답변 직접 입력/), "커스텀");
    // With the stateful harness, value accumulates, so the last onChange carries
    // the full string.
    expect(spy).toHaveBeenLastCalledWith("커스텀");
  });
});
