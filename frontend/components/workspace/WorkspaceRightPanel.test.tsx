import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { WorkspaceRightPanel, deriveMode } from "./WorkspaceRightPanel";

const QP = { interrupt_id: "i-1", questions: {
  name: "q", preamble: null, parse_ok: true, raw_markdown: null,
  questions: [{ number: 1, category: null, text: "누구?", answer: null,
    options: [{ letter: "A", text: "PM", is_other: false, recommended: true }] }] } };

// Default with no document for the drawer to open — for tests that do not look at "{이름} 보기" (View {name}).
const NO_DOC = { currentDocName: null, onOpenDoc: () => {} };

describe("WorkspaceRightPanel mode switching", () => {
  it("renders QuestionForm when pendingQuestions is set", () => {
    render(<WorkspaceRightPanel {...NO_DOC} projectId="p1" pendingQuestions={QP}
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
    render(<WorkspaceRightPanel {...NO_DOC} projectId="p1" pendingQuestions={null}
      stages={[{ stage: "Prototype & Validation", status: "in_progress", summary: "" }]}
      changedPaths={[]} onSubmitAnswers={vi.fn()} busy={false} />);
    expect(screen.getByLabelText("프로토타입 프리뷰")).toBeInTheDocument();
  });

  it("renders recent artifacts otherwise", () => {
    render(<WorkspaceRightPanel {...NO_DOC} projectId="p1" pendingQuestions={null} stages={[]}
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

describe("deriveMode — 답변 제출 직후 프리뷰로 튀지 않는다 (regression)", () => {
  // Observed bug (question2.png): when the prototype stage is in_progress and a question is
  // showing, submitting an answer makes submitAnswers set pendingQuestions to null at once.
  // At that moment the priority drops to preview, the question form turns into the prototype
  // viewer, and when the next question arrives it goes back to the form — to the user the screen
  // looks like it flips around on its own. While a turn is running, do not switch to the
  // preview.
  const protoActive = [
    { stage: "Prototype & Validation", status: "in_progress" as const, summary: "" },
  ];

  it("stays out of preview while a turn is still streaming", () => {
    expect(deriveMode(null, protoActive, true)).not.toBe("preview");
  });

  it("shows preview once the turn settles with no question pending", () => {
    expect(deriveMode(null, protoActive, false)).toBe("preview");
  });

  it("still prefers a pending question over everything while streaming", () => {
    // A new question arrived during streaming — a question always takes top priority.
    expect(deriveMode(QP, protoActive, true)).toBe("questions");
  });

  it("defaults to the settled behaviour when streaming is not passed", () => {
    // Existing call sites that pass no argument (including tests) must not break.
    expect(deriveMode(null, protoActive)).toBe("preview");
  });
});

describe("WorkspaceRightPanel — 스트리밍 중 프로토타입 스테이지", () => {
  it("keeps showing artifacts instead of flipping to the prototype viewer", () => {
    render(<WorkspaceRightPanel {...NO_DOC} projectId="p1" pendingQuestions={null}
      stages={[{ stage: "Prototype & Validation", status: "in_progress", summary: "" }]}
      changedPaths={["aiplc-docs/audit.md"]} onSubmitAnswers={vi.fn()} busy={true} />);
    expect(screen.queryByLabelText("프로토타입 프리뷰")).toBeNull();
    expect(screen.getByText("aiplc-docs/audit.md")).toBeInTheDocument();
  });
});

describe("WorkspaceRightPanel 문서 보기", () => {
  it("질문 폼 위에 드로어가 열 문서의 이름으로 버튼을 둔다", async () => {
    const onOpenDoc = vi.fn();
    render(<WorkspaceRightPanel projectId="p1" pendingQuestions={QP} stages={[]} changedPaths={[]}
      onSubmitAnswers={vi.fn()} busy={false} currentDocName="prfaq.md" onOpenDoc={onOpenDoc} />);
    screen.getByRole("button", { name: /prfaq\.md 보기/ }).click();
    expect(onOpenDoc).toHaveBeenCalledOnce();
  });

  it("열 문서가 없으면 버튼을 그리지 않는다", () => {
    render(<WorkspaceRightPanel {...NO_DOC} projectId="p1" pendingQuestions={QP} stages={[]}
      changedPaths={[]} onSubmitAnswers={vi.fn()} busy={false} />);
    expect(screen.queryByRole("button", { name: /보기/ })).toBeNull();
  });
});
