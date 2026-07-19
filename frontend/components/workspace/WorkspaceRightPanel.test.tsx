import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { WorkspaceRightPanel } from "./WorkspaceRightPanel";

const QP = { interrupt_id: "i-1", questions: {
  name: "q", preamble: null, parse_ok: true, raw_markdown: null,
  questions: [{ number: 1, category: null, text: "누구?", answer: null,
    options: [{ letter: "A", text: "PM", is_other: false, recommended: true }] }] } };

describe("WorkspaceRightPanel mode switching", () => {
  it("renders QuestionForm when pendingQuestions is set", () => {
    render(<WorkspaceRightPanel projectId="p1" pendingQuestions={QP}
      stages={[]} changedPaths={[]} onSubmitAnswers={vi.fn()} busy={false} />);
    // QuestionCard renders "Q{number}. {text}" as sibling text nodes (see
    // QuestionCard.tsx), so the question text isn't its own exact text node —
    // matched here the same way the existing questions-domain tests do
    // (regex over the merged node text, e.g. QuestionForm.test.tsx's
    // `/Q1\. 이 제품을 시장/`).
    expect(screen.getByText(/누구\?/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /답변 제출/ })).toBeInTheDocument();
  });

  it("renders preview when the prototype stage is active and no questions pend", () => {
    render(<WorkspaceRightPanel projectId="p1" pendingQuestions={null}
      stages={[{ stage: "Prototype & Validation", status: "in_progress", summary: "" }]}
      changedPaths={[]} onSubmitAnswers={vi.fn()} busy={false} />);
    expect(screen.getByLabelText("프로토타입 프리뷰")).toBeInTheDocument();
  });

  it("renders recent artifacts otherwise", () => {
    render(<WorkspaceRightPanel projectId="p1" pendingQuestions={null} stages={[]}
      changedPaths={["aiplc-docs/audit.md"]} onSubmitAnswers={vi.fn()} busy={false} />);
    expect(screen.getByText("aiplc-docs/audit.md")).toBeInTheDocument();
  });
});
