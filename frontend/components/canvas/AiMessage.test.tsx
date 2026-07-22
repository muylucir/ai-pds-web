// frontend/components/canvas/AiMessage.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AiMessage } from "./AiMessage";
import type { AiItem } from "@/lib/useTurnStream";

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
    expect(screen.getByText("추론 과정")).toBeInTheDocument();
    expect(screen.getByText(/prototype\/src\/App\.tsx/)).toBeInTheDocument();
  });

  it("shows a typing indicator while streaming with no text yet", () => {
    // 정적 "AI가 작성 중…" 문구는 점 3개 bounce 인디케이터로 대체됐다
    // (activity-indicator spec) — 접근성 라벨로 같은 의도를 검증한다.
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
});

describe("AiMessage — 활동 인디케이터 (멈춘 것처럼 보이는 문제)", () => {
  it("스트리밍 중 빈 텍스트면 타이핑 애니메이션 인디케이터를 렌더한다", () => {
    render(<AiMessage item={{ ...base, streaming: true }} />);
    const indicator = screen.getByLabelText("AI가 작성 중");
    expect(indicator).toBeInTheDocument();
    // 점 3개 bounce 애니메이션
    expect(indicator.querySelectorAll(".animate-bounce")).toHaveLength(3);
  });

  it("스트리밍 중 status trace가 있으면 마지막 도구 활동을 한글 라벨로 상시 표시한다", () => {
    render(
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
    expect(screen.getByText("질문을 준비하고 있어요…")).toBeInTheDocument();
    // 마지막 status만 — 이전 활동(file_read)은 라이브 라인에 없음
    expect(screen.queryByText("자료를 확인하고 있어요…")).not.toBeInTheDocument();
  });

  it("file_changed는 활동 라인 대상이 아니다 — 마지막 status가 유지된다", () => {
    render(
      <AiMessage
        item={{
          ...base,
          streaming: true,
          text: "작성 중",
          trace: [
            { kind: "status", text: "file_write", path: null },
            { kind: "file_changed", text: null, path: "aiplc-docs/x.md" },
          ],
        }}
      />,
    );
    expect(screen.getByText("문서를 작성하고 있어요…")).toBeInTheDocument();
  });

  it("스트리밍이 끝나면 활동 라인이 사라진다", () => {
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
    expect(screen.queryByText("질문을 준비하고 있어요…")).not.toBeInTheDocument();
  });

  it("알 수 없는 도구명은 폴백 문구로 표시한다", () => {
    render(
      <AiMessage
        item={{
          ...base,
          streaming: true,
          text: "…",
          trace: [{ kind: "status", text: "custom_tool", path: null }],
        }}
      />,
    );
    expect(screen.getByText("custom_tool 실행 중…")).toBeInTheDocument();
  });
});
