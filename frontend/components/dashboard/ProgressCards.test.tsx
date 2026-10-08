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
    // 분모는 스테이지 수(3)가 아니라 이 경로가 거치는 capability 수다.
    expect(screen.getByText("/ 4")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();   // artifacts
  });

  // 질문 파일 개수는 "대기 중인 질문 수"가 아니다. 질문은 AskUserQuestion으로
  // 전달되고 답변도 그 왕복으로 돌아오므로 파일의 `[Answer]:`는 영구히 비어
  // 있다(discovery-config/CLAUDE.md의 override 섹션). 그 값을 미답변 수로 쓰면
  // 사용자가 전부 답한 뒤에도 "대기 중인 질문 3"이 남고, 링크를 눌러도 답할
  // 것이 없다 — 파일은 UI에서 편집할 수 없다.
  it("labels the question-file count as a record, not as pending answers", () => {
    render(<ProgressCards state={STATE} questionFileCount={3} artifactCount={7} />);
    expect(screen.getByText("질문 기록")).toBeInTheDocument();
    expect(screen.queryByText("대기 중인 질문")).not.toBeInTheDocument();
  });

  // 그 링크는 `/projects/{id}/questions`를 가리켰는데, 그 라우트는 은퇴해
  // `?file=`을 버리고 /workspace로 리다이렉트한다. 답변할 곳은 워크스페이스의
  // 질문 폼이므로 처음부터 거기로 보낸다.
  it("does not offer an answer-questions call to action (answers live in the workspace form)", () => {
    render(<ProgressCards state={STATE} questionFileCount={3} artifactCount={7} />);
    expect(screen.queryByRole("link", { name: /질문 답변하기/ })).not.toBeInTheDocument();
  });
});
