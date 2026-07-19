import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { WorkspaceRightPanel, deriveMode } from "./WorkspaceRightPanel";

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

describe("deriveMode — latest-status-by-stage-name (regression)", () => {
  // useWorkspaceStream's `stages` is an append-only raw event log (a stage's
  // later "completed" event is a SEPARATE array entry, not an overwrite of
  // the earlier "in_progress" one). deriveMode must look at each stage's
  // LATEST event, not just whether an in_progress event for it ever
  // occurred — otherwise once Prototype & Validation has been in_progress
  // even once, the panel is stuck on "preview" forever, even after that
  // stage completes and a later, unrelated stage becomes in_progress.
  it("does not get stuck on preview after the prototype stage completes and a later stage starts", () => {
    const mode = deriveMode(null, [
      { stage: "Prototype & Validation", status: "in_progress", summary: "" },
      { stage: "Prototype & Validation", status: "completed", summary: "" },
      { stage: "Go-to-Market", status: "in_progress", summary: "" },
    ]);
    expect(mode).toBe("artifacts");
  });

  it("still shows preview while the prototype stage's LATEST status is in_progress", () => {
    const mode = deriveMode(null, [
      { stage: "Prototype & Validation", status: "in_progress", summary: "1차" },
      { stage: "Prototype & Validation", status: "in_progress", summary: "2차" },
    ]);
    expect(mode).toBe("preview");
  });
});
