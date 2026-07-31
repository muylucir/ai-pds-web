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
    expect(screen.getByText("질문을 준비하고 있어요")).toBeInTheDocument();
    // 마지막 status만 — 이전 활동(file_read)은 라이브 라인에 없음
    expect(screen.queryByText("자료를 확인하고 있어요")).not.toBeInTheDocument();
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
    expect(screen.getByText("문서를 작성하고 있어요")).toBeInTheDocument();
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
    expect(screen.getByText("추론 과정")).toBeInTheDocument();
  });

  it("텍스트가 있으면 말풍선을 그린다", () => {
    render(<AiMessage item={{ ...base, streaming: false, text: "완료" }} />);
    expect(screen.getByTestId("ai-bubble")).toBeInTheDocument();
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
    expect(screen.queryByText("질문을 준비하고 있어요")).not.toBeInTheDocument();
  });

  it("도구가 아직 하나도 안 돌았어도 진행 표시가 뜬다 — 그 구간이 가장 불안하다", () => {
    // 종전에는 status trace가 있어야만 활동 라인이 나왔다. 턴 시작 직후
    // 모델이 생각만 하는 구간(가장 길다)에 아무 표시도 없던 것이 "멈춘 것
    // 같다"의 주된 원인이었다.
    render(<AiMessage item={{ ...base, streaming: true, text: "분석을 시작합니다." }} />);
    expect(screen.getByRole("status")).toHaveTextContent("생각하고 있어요");
  });

  it("경과 시간을 함께 보여준다 — 3초짜리와 40초짜리를 구분할 근거", () => {
    render(<AiMessage item={{ ...base, streaming: true, text: "작업 중" }} />);
    expect(screen.getByRole("status")).toHaveTextContent("0초");
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
    expect(screen.getByText("custom_tool 실행 중")).toBeInTheDocument();
  });
});

describe("Claude Agent SDK 도구명 라벨 (regression)", () => {
  // 드라이버가 바뀌면 status 이벤트의 도구 이름이 SDK 내장 이름으로 온다.
  // 매핑에 없으면 폴백이 발동해 사용자에게 "Write 실행 중…" 같은 영어 도구명이
  // 노출된다 — 크래시는 아니지만 UX가 조용히 나빠진다.
  const CASES: Array<[string, RegExp]> = [
    ["AskUserQuestion", /질문을 준비하고 있어요/],
    ["Write", /문서를 작성하고 있어요/],
    ["Edit", /문서를 작성하고 있어요/],
    ["MultiEdit", /문서를 작성하고 있어요/],
    ["Read", /자료를 확인하고 있어요/],
    ["Glob", /자료를 찾고 있어요/],
    // CLI 기본 도구 (tools=None이므로 사용 가능; envision.md의 URL 분석 모드 B/C와 workspace 탐색 필요)
    ["Grep", /자료를 찾고 있어요/],
    ["WebFetch", /정보를 수집하고 있어요/],
    ["Bash", /작업을 진행하고 있어요/],
  ];

  for (const [tool, label] of CASES) {
    it(`maps ${tool} to a Korean activity label`, () => {
      render(
        <AiMessage
          item={{
            id: "a1", role: "ai", text: "", streaming: true, error: null,
            trace: [{ kind: "status", text: tool, path: null }],
          }}
        />,
      );
      expect(screen.getByText(label)).toBeInTheDocument();
      // 영어 도구명이 그대로 보이면 안 된다.
      expect(screen.queryByText(new RegExp(`${tool} 실행 중`))).toBeNull();
    });
  }

  it("keeps the Strands tool names working during the env-toggle period", () => {
    // 두 드라이버가 공존하는 기간에는 양쪽 다 올바른 라벨이 나와야 한다.
    render(
      <AiMessage
        item={{
          id: "a1", role: "ai", text: "", streaming: true, error: null,
          trace: [{ kind: "status", text: "file_write", path: null }],
        }}
      />,
    );
    expect(screen.getByText(/문서를 작성하고 있어요/)).toBeInTheDocument();
  });
});
