import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ProgressCards } from "./ProgressCards";
import type { ProjectState } from "@/lib/api/types";

const STATE: ProjectState = {
  project_type: "discovery",
  current_stage: "Prototype & Validation",
  stages: [
    { name: "Envision", status: "completed", note: null },
    { name: "Solution Analysis", status: "completed", note: null },
    { name: "Prototype & Validation", status: "in_progress", note: null },
  ],
  capabilities: [
    { key: "envision", name: "Envision", status: "completed", note: null },
    { key: "use_case_intake", name: "Use Case Intake", status: "not_applicable", note: null },
    { key: "prioritize", name: "Prioritize", status: "not_applicable", note: null },
    { key: "prototype", name: "Prototype", status: "in_progress", note: null },
    { key: "product_strategy", name: "Product Strategy", status: "pending", note: null },
    { key: "go_to_market", name: "Go-to-Market", status: "pending", note: null },
  ],
};

describe("ProgressCards", () => {
  it("shows overall progress and completed-stage counts", () => {
    render(<ProgressCards state={STATE} questionFileCount={3} artifactCount={7} />);
    expect(screen.getByText("1")).toBeInTheDocument();   // completed capabilities
    // The denominator is not the number of stages (3) but the number of capabilities this path goes through.
    expect(screen.getByText("/ 4")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();   // artifacts
  });

  // The question file count is not the "number of pending questions". Questions are
  // delivered via AskUserQuestion and answers come back on that round trip, so the file's
  // `[Answer]:` stays empty forever (the override section of discovery-config/CLAUDE.md).
  // Using that value as the unanswered count leaves "3 pending questions" even after the user
  // answered everything, and clicking the link gives nothing to answer — the file is not editable in the UI.
  it("labels the question-file count as a record, not as pending answers", () => {
    render(<ProgressCards state={STATE} questionFileCount={3} artifactCount={7} />);
    expect(screen.getByText("질문 기록")).toBeInTheDocument();
    expect(screen.queryByText("대기 중인 질문")).not.toBeInTheDocument();
  });

  // That link pointed to `/projects/{id}/questions`, but that route is retired; it drops
  // `?file=` and redirects to /workspace. The place to answer is the workspace's question
  // form, so send users there from the start.
  it("does not offer an answer-questions call to action (answers live in the workspace form)", () => {
    render(<ProgressCards state={STATE} questionFileCount={3} artifactCount={7} />);
    expect(screen.queryByRole("link", { name: /질문 답변하기/ })).not.toBeInTheDocument();
  });
});
