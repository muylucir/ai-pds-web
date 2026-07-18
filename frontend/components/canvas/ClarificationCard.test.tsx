import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ClarificationCard } from "./ClarificationCard";
import { clarificationQuestions } from "@/test/fixtures/clarificationQuestions";

// The fixture's single question already carries answer:"C" (the pilot's
// resolved history from Plan A/B's wizard fixtures) — an unanswered variant is
// derived here so the card renders as the still-open interaction the mockup
// depicts (an unresolved contradiction with live option buttons).
const unanswered = {
  ...clarificationQuestions,
  questions: clarificationQuestions.questions.map((q) => ({ ...q, answer: null })),
};

describe("ClarificationCard", () => {
  it("renders the contradiction heading, preamble, and per-question category/text/options", () => {
    render(<ClarificationCard file={unanswered} onChoose={vi.fn()} busy={false} />);
    expect(screen.getByText("답변 간 모순 감지 — 게이트 보류")).toBeInTheDocument();
    expect(screen.getByText(unanswered.preamble!)).toBeInTheDocument();
    expect(screen.getByText(unanswered.questions[0].category!)).toBeInTheDocument();
    expect(screen.getByText(unanswered.questions[0].text)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /아직 정하지 않음/ })).toBeInTheDocument();
  });

  it("clicking an option calls onChoose with 'letter — text'", async () => {
    const user = userEvent.setup();
    const onChoose = vi.fn();
    render(<ClarificationCard file={unanswered} onChoose={onChoose} busy={false} />);
    await user.click(screen.getByRole("button", { name: /아직 정하지 않음/ }));
    expect(onChoose).toHaveBeenCalledWith("C — 아직 정하지 않음 — 파일럿 운영 중 데이터로 결정");
  });

  it("disables option buttons while busy", () => {
    render(<ClarificationCard file={unanswered} onChoose={vi.fn()} busy={true} />);
    expect(screen.getByRole("button", { name: /아직 정하지 않음/ })).toBeDisabled();
  });
});
