// frontend/components/canvas/ReasoningTrace.test.tsx
//
// 추론 과정 아코디언은 도구가 **무엇을 했는지** 보여야 한다. Write는 별도
// `file_changed` 이벤트가 경로를 들고 오므로 처음부터 `📝 파일 변경: …`으로 보였지만
// Read/Bash는 `status` 이벤트에 이름만 실려 `Read`, `Bash`만 떴다 — 무엇을 읽었는지,
// 무슨 명령을 돌렸는지가 트레이스의 요점인데 그것이 빠져 있었다.
//
// 값은 백엔드(`backend/pathfinder/tool_trace.py`)가 만들고 아이콘·구분자만 여기서
// 붙인다. 도구 이름은 고유명이라 번역하지 않는다.
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReasoningTrace } from "./ReasoningTrace";
import type { TraceEntry } from "@/lib/useTurnStream";

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
      entry({ text: "mcp__pathfinder__report_stage" })]} />);
    expect(screen.getByText("mcp__pathfinder__report_stage")).toBeInTheDocument();
  });

  it("항목이 없으면 아코디언 자체를 그리지 않는다", () => {
    const { container } = render(<ReasoningTrace entries={[]} />);
    expect(container.querySelector("details")).toBeNull();
  });
});
