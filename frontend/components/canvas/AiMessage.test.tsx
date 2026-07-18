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

  it("shows a typing hint while streaming with no text yet", () => {
    render(<AiMessage item={{ ...base, streaming: true }} />);
    expect(screen.getByText(/작성 중/)).toBeInTheDocument();
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
});
