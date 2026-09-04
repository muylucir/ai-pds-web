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
    expect(screen.getByText("진행 기록")).toBeInTheDocument();
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

  it("중단된 턴은 말풍선 아래에 그 사실을 남긴다", () => {
    // trace의 한 줄로 넣지 않는다 — trace는 도구 실행 기록이고 중단은 턴의
    // 종결 사유다. 접혀 있는 "추론 과정" 안에 두면 왜 말이 끊겼는지 보이지 않는다.
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
    // 점 3개 bounce 애니메이션
    expect(indicator.querySelectorAll(".animate-bounce")).toHaveLength(3);
  });

  it("스트리밍 중에는 진행 표시도 진행 기록도 그리지 않는다", () => {
    // 진행 상황은 입력창 위 고정 줄(LiveActivityBar)로 옮겼다. 말풍선 옆에서
    // 함께 갱신되던 것이 텍스트 스트리밍과 겹쳐 산만했던 것이 이유다.
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
    // 중단된 턴(유휴 타임아웃, SSE 끊김)이 이 모양으로 복원된다: text=""이고
    // trace만 있다. streaming이 false라 타이핑 점도 뜨지 않으므로 내용 없는
    // 회색 상자만 남는데, 라이브에서 그 자리에 있던 것은 진행 표시였고 그것은
    // 복원 대상이 아니다. 트레이스는 무엇까지 돌렸는지의 유일한 기록이므로
    // 유지한다.
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
