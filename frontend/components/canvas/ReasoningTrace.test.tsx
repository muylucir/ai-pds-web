// frontend/components/canvas/ReasoningTrace.test.tsx
//
// The reasoning accordion must show **what** each tool did. Write showed `📝 파일 변경: …`
// from the start because a separate `file_changed` event carries the path, but Read/Bash only
// had their name in the `status` event and showed just `Read`, `Bash` — what was read and which
// command was run are the point of the trace, and those were missing.
//
// The backend (`backend/aipds/tool_trace.py`) builds the values; only the icons and separators
// are added here. Tool names are proper nouns and are not translated.
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReasoningTrace } from "./ReasoningTrace";
import type { TraceEntry } from "@/lib/chatItems";

function entry(p: Partial<TraceEntry>): TraceEntry {
  return { kind: "status", text: null, path: null, detail: null, ...p };
}

describe("ReasoningTrace", () => {
  it("파일 도구는 경로를 라벨과 함께 보여준다 (기존 동작)", () => {
    render(<ReasoningTrace entries={[
      entry({ kind: "file_changed", path: "aiplc-docs/audit.md" })]} />);
    expect(screen.getByText(/📝.*aiplc-docs\/audit\.md/)).toBeInTheDocument();
  });

  it("Read는 읽은 파일을, Bash는 돌린 명령을 함께 보여준다", () => {
    render(<ReasoningTrace entries={[
      entry({ text: "Read", detail: "aiplc-docs/discovery/envision/x.md" }),
      entry({ text: "Bash", detail: "ls -la aiplc-docs/" }),
    ]} />);
    expect(screen.getByText("🔍 Read · aiplc-docs/discovery/envision/x.md"))
      .toBeInTheDocument();
    expect(screen.getByText("⌘ Bash · ls -la aiplc-docs/")).toBeInTheDocument();
  });

  it("detail이 없으면 이름만 — 빈 구분자를 남기지 않는다", () => {
    render(<ReasoningTrace entries={[entry({ text: "Read" })]} />);
    expect(screen.getByText("🔍 Read")).toBeInTheDocument();
  });

  it("모르는 도구는 아이콘 없이 이름만 (잘못된 아이콘보다 없는 편이 낫다)", () => {
    render(<ReasoningTrace entries={[
      entry({ text: "mcp__aipds__report_stage" })]} />);
    expect(screen.getByText("mcp__aipds__report_stage")).toBeInTheDocument();
  });

  it("항목이 없으면 아코디언 자체를 그리지 않는다", () => {
    const { container } = render(<ReasoningTrace entries={[]} />);
    expect(container.querySelector("details")).toBeNull();
  });

  it("사고 구간은 전용 줄로 보여준다", () => {
    // Thinking **text** is not on this path (measured 0 characters on Bedrock). What exists is
    // the span in which the model thought, and that makes the trace a timeline of the turn:
    // think → Read → think → write.
    render(<ReasoningTrace entries={[entry({ kind: "thinking" })]} />);
    expect(screen.getByText("🧠 생각")).toBeInTheDocument();
  });

  it("항상 접힌 채로 시작한다 — 도는 동안에는 AiMessage가 아예 렌더하지 않는다", () => {
    const { container } = render(
      <ReasoningTrace entries={[entry({ text: "Read" })]} />);
    expect(container.querySelector("details")).not.toHaveAttribute("open");
  });
});

describe("ReasoningTrace — 서브에이전트", () => {
  // The start is not recorded: the Agent tool call already arrives as the orchestrating agent's status, carrying
  // what was delegated (that status line, not `🤖`, holds that fact).
  // Only the end comes here.
  it("완료한 에이전트를 이름과 요약으로 남긴다", () => {
    render(<ReasoningTrace entries={[entry({
      kind: "agent", text: "화면 골격", detail: "app/page.tsx를 만들었다",
      status: "completed" })]} />);
    expect(screen.getByText("✅ 에이전트 완료: 화면 골격 · app/page.tsx를 만들었다"))
      .toBeInTheDocument();
  });

  it("실패는 완료와 다른 줄로 보인다", () => {
    // With the same icon and the same label, the trace cannot say what went wrong.
    render(<ReasoningTrace entries={[entry({
      kind: "agent", text: "데이터 모델", detail: "스키마를 못 만들었다",
      status: "failed" })]} />);
    expect(screen.getByText(/⚠️ 에이전트 실패: 데이터 모델/)).toBeInTheDocument();
  });

  it("중단·강제종료도 성공으로 보이지 않는다", () => {
    // Only `completed` is success — drawing stopped/killed as success would make a build the
    // user stopped read as a normal completion.
    for (const status of ["stopped", "killed"]) {
      const { unmount } = render(<ReasoningTrace entries={[entry({
        kind: "agent", text: "스타일", status })]} />);
      expect(screen.getByText(/⚠️/)).toBeInTheDocument();
      unmount();
    }
  });

  it("이름이나 요약이 없어도 줄이 성립한다", () => {
    render(<ReasoningTrace entries={[entry({
      kind: "agent", text: null, detail: null, status: "completed" })]} />);
    expect(screen.getByText("✅ 에이전트 완료")).toBeInTheDocument();
  });
});
