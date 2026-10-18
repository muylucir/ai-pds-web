// frontend/components/canvas/AiMessage.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AiMessage } from "./AiMessage";
import type { AiItem } from "@/lib/chatItems";

const base: AiItem = { id: "a1", role: "ai", text: "", trace: [], streaming: false, error: null };

describe("AiMessage", () => {
  it("renders the accumulated text and a reasoning trace", () => {
    render(
      <AiMessage
        item={{
          ...base,
          text: "필터를 추가했습니다.",
          trace: [
            { kind: "status", text: "분석 중…", path: null },
            { kind: "file_changed", text: null, path: "prototype/src/App.tsx" },
          ],
        }}
      />,
    );
    expect(screen.getByText("필터를 추가했습니다.")).toBeInTheDocument();
    expect(screen.getByText("진행 기록")).toBeInTheDocument();
    expect(screen.getByText(/prototype\/src\/App\.tsx/)).toBeInTheDocument();
  });

  it("shows a typing indicator while streaming with no text yet", () => {
    // The static "AI가 작성 중…" (AI is writing…) text was replaced by a three-dot bounce indicator
    // (activity-indicator spec) — verify the same intent through the accessibility label.
    render(<AiMessage item={{ ...base, streaming: true }} />);
    expect(screen.getByLabelText("AI가 작성 중")).toBeInTheDocument();
  });

  it("shows an error line when the turn errored", () => {
    render(<AiMessage item={{ ...base, error: "빌드에 실패했습니다" }} />);
    expect(screen.getByText(/빌드에 실패했습니다/)).toBeInTheDocument();
  });

  it("announces the accumulating answer text via an aria-live region", () => {
    render(<AiMessage item={{ ...base, text: "필터를 추가했습니다." }} />);
    const textEl = screen.getByText("필터를 추가했습니다.");
    expect(textEl.closest('[aria-live="polite"]')).not.toBeNull();
  });

  it("renders markdown in the AI bubble", () => {
    render(<AiMessage item={{ id: "1", role: "ai", text: "**중요**", trace: [], streaming: false, error: null }} />);
    expect(screen.getByText("중요").tagName).toBe("STRONG");
  });

  it("중단된 턴은 말풍선 아래에 그 사실을 남긴다", () => {
    // Not added as a trace line — the trace is the record of tool runs, and an interruption is the turn's
    // termination reason. Inside the collapsed "추론 과정" (reasoning process), you could not see why the message stopped.
    render(
      <AiMessage
        item={{ ...base, streaming: false, text: "분석하다가", interrupted: true }}
      />,
    );
    expect(screen.getByText("중단됨")).toBeInTheDocument();
  });

  it("정상 종료된 턴에는 중단 표시가 없다", () => {
    render(<AiMessage item={{ ...base, streaming: false, text: "완료" }} />);
    expect(screen.queryByText("중단됨")).not.toBeInTheDocument();
  });
});

describe("AiMessage — 진행 상황은 여기가 아니다", () => {
  it("스트리밍 중 빈 텍스트면 타이핑 애니메이션 인디케이터를 렌더한다", () => {
    render(<AiMessage item={{ ...base, streaming: true }} />);
    const indicator = screen.getByLabelText("AI가 작성 중");
    expect(indicator).toBeInTheDocument();
    // three-dot bounce animation
    expect(indicator.querySelectorAll(".animate-bounce")).toHaveLength(3);
  });

  it("스트리밍 중에는 진행 표시도 진행 기록도 그리지 않는다", () => {
    // Progress moved to the fixed line above the input box (LiveActivityBar). The reason: updating it
    // next to the bubble overlapped with text streaming and was distracting.
    const { container } = render(
      <AiMessage
        item={{
          ...base,
          streaming: true,
          text: "분석을 시작합니다.",
          trace: [
            { kind: "status", text: "file_read", path: null },
            { kind: "status", text: "ask_questions", path: null },
          ],
        }}
      />,
    );
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByText("질문을 준비하고 있어요")).not.toBeInTheDocument();
    expect(container.querySelector("details")).toBeNull();
  });

  it("턴이 끝나면 같은 기록이 접힌 채로 붙는다", () => {
    const { container } = render(
      <AiMessage
        item={{
          ...base,
          streaming: false,
          text: "정리했습니다.",
          trace: [
            { kind: "thinking", text: null, path: null },
            { kind: "status", text: "Read", path: null },
          ],
        }}
      />,
    );
    const acc = container.querySelector("details");
    expect(acc).not.toBeNull();
    expect(acc).not.toHaveAttribute("open");
    expect(screen.getByText("진행 기록")).toBeInTheDocument();
  });

  it("복원된 턴에 텍스트가 없으면 빈 말풍선을 그리지 않는다", () => {
    // An interrupted turn (idle timeout, SSE disconnect) is restored in this shape: text="" with
    // only a trace. streaming is false, so the typing dots do not appear either, leaving an empty
    // gray box; what occupied that spot live was the progress indicator, and that is not
    // something to restore. The trace is the only record of how far the turn ran, so it is
    // kept.
    render(
      <AiMessage
        item={{
          ...base,
          streaming: false,
          text: "",
          trace: [{ kind: "status", text: "Read", path: null }],
        }}
      />,
    );
    expect(screen.queryByTestId("ai-bubble")).not.toBeInTheDocument();
    expect(screen.getByText("진행 기록")).toBeInTheDocument();
  });

  it("텍스트가 있으면 말풍선을 그린다", () => {
    render(<AiMessage item={{ ...base, streaming: false, text: "완료" }} />);
    expect(screen.getByTestId("ai-bubble")).toBeInTheDocument();
  });

  it("끝난 턴에는 활동 문구가 남지 않는다 — 진행 표시는 화면의 고정 줄이 갖는다", () => {
    render(
      <AiMessage
        item={{
          ...base,
          streaming: false,
          text: "완료했습니다.",
          trace: [{ kind: "status", text: "ask_questions", path: null }],
        }}
      />,
    );
    expect(screen.queryByText("질문을 준비하고 있어요")).not.toBeInTheDocument();
  });
});
