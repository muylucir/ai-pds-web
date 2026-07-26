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

describe("deriveMode — 답변 제출 직후 프리뷰로 튀지 않는다 (regression)", () => {
  // 실측 버그(question2.png): 프로토타입 스테이지가 in_progress인 채로 질문이
  // 떠 있을 때 답변을 제출하면 submitAnswers가 pendingQuestions를 즉시 null로
  // 만든다. 그 순간 우선순위가 preview로 내려앉아 질문 폼이 프로토타입 뷰어로
  // 바뀌고, 다음 질문이 도착하면 다시 폼으로 돌아온다 — 사용자에게는 화면이
  // 제멋대로 뒤바뀌는 것으로 보인다. 턴이 도는 동안에는 프리뷰로 전환하지
  // 않는다.
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
    // 스트리밍 중 새 질문이 도착한 경우 — 질문이 언제나 최우선이다.
    expect(deriveMode(QP, protoActive, true)).toBe("questions");
  });

  it("defaults to the settled behaviour when streaming is not passed", () => {
    // 인자를 넘기지 않는 기존 호출부(테스트 포함)가 깨지지 않아야 한다.
    expect(deriveMode(null, protoActive)).toBe("preview");
  });
});

describe("WorkspaceRightPanel — 스트리밍 중 프로토타입 스테이지", () => {
  it("keeps showing artifacts instead of flipping to the prototype viewer", () => {
    render(<WorkspaceRightPanel projectId="p1" pendingQuestions={null}
      stages={[{ stage: "Prototype & Validation", status: "in_progress", summary: "" }]}
      changedPaths={["aiplc-docs/audit.md"]} onSubmitAnswers={vi.fn()} busy={true} />);
    expect(screen.queryByLabelText("프로토타입 프리뷰")).toBeNull();
    expect(screen.getByText("aiplc-docs/audit.md")).toBeInTheDocument();
  });
});
