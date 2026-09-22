// frontend/components/canvas/ReasoningTrace.test.tsx
//
// 추론 과정 아코디언은 도구가 **무엇을 했는지** 보여야 한다. Write는 별도
// `file_changed` 이벤트가 경로를 들고 오므로 처음부터 `📝 파일 변경: …`으로 보였지만
// Read/Bash는 `status` 이벤트에 이름만 실려 `Read`, `Bash`만 떴다 — 무엇을 읽었는지,
// 무슨 명령을 돌렸는지가 트레이스의 요점인데 그것이 빠져 있었다.
//
// 값은 백엔드(`backend/aipds/tool_trace.py`)가 만들고 아이콘·구분자만 여기서
// 붙인다. 도구 이름은 고유명이라 번역하지 않는다.
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
    // 사고 **텍스트**는 이 경로에 없다(Bedrock 실측 0자). 있는 것은 모델이
    // 생각한 구간이고, 그것이 트레이스를 턴의 타임라인으로 만든다:
    // 생각 → Read → 생각 → 작성.
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
  // 시작은 남기지 않는다: Agent 도구 호출이 이미 총괄의 status로 들어오고 거기에
  // 무엇을 맡겼는지까지 실려 있다(`🤖`가 아니라 그 status 줄이 그 사실을 갖는다).
  // 여기 오는 것은 종료뿐이다.
  it("완료한 에이전트를 이름과 요약으로 남긴다", () => {
    render(<ReasoningTrace entries={[entry({
      kind: "agent", text: "화면 골격", detail: "app/page.tsx를 만들었다",
      status: "completed" })]} />);
    expect(screen.getByText("✅ 에이전트 완료: 화면 골격 · app/page.tsx를 만들었다"))
      .toBeInTheDocument();
  });

  it("실패는 완료와 다른 줄로 보인다", () => {
    // 같은 아이콘·같은 라벨이면 트레이스가 무엇이 잘못됐는지 말하지 못한다.
    render(<ReasoningTrace entries={[entry({
      kind: "agent", text: "데이터 모델", detail: "스키마를 못 만들었다",
      status: "failed" })]} />);
    expect(screen.getByText(/⚠️ 에이전트 실패: 데이터 모델/)).toBeInTheDocument();
  });

  it("중단·강제종료도 성공으로 보이지 않는다", () => {
    // `completed`만 성공이다 — stopped/killed를 성공으로 그리면 사용자가 끊은
    // 빌드가 정상 완료로 읽힌다.
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
