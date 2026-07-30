// frontend/components/prototypes/BuildPanel.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BuildPanel } from "./BuildPanel";
import * as prototypeStream from "@/lib/usePrototypeStream";
import * as prototypesApi from "@/lib/api/prototypes";
import { ApiError } from "@/lib/api/client";

vi.mock("@/lib/api/prototypes", async (orig) => ({
  ...(await orig<typeof import("@/lib/api/prototypes")>()),
  closeSession: vi.fn().mockResolvedValue(undefined),
  startHost: vi.fn().mockResolvedValue({ state: "running", port: 4001, log_tail: "" }),
}));

const QP = {
  interrupt_id: "i-1",
  questions: {
    name: "q",
    preamble: null,
    parse_ok: true,
    raw_markdown: null,
    questions: [
      {
        number: 1,
        category: null,
        text: "누구?",
        answer: null,
        options: [{ letter: "A", text: "PM", is_other: false, recommended: true }],
      },
    ],
  },
};

function mockStream(overrides: Partial<prototypeStream.PrototypeStream> = {}) {
  const base: prototypeStream.PrototypeStream = {
    items: [],
    streaming: false,
    pendingQuestions: null,
    buildComplete: null,
    changedPaths: [],
    startBuild: vi.fn(),
    send: vi.fn(),
    submitAnswers: vi.fn().mockResolvedValue(undefined),
    interrupt: vi.fn().mockResolvedValue(undefined),
    restartForImprovement: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
  vi.spyOn(prototypeStream, "usePrototypeStream").mockReturnValue(base);
  return base;
}

describe("BuildPanel", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders AI/user chat items from the stream", () => {
    mockStream({
      items: [
        { id: "1", role: "user", text: "안녕" },
        { id: "2", role: "ai", text: "네, 시작합니다", trace: [], streaming: false, error: null },
      ],
    });
    render(<BuildPanel projectId="p1" slug="todo-app" onClose={vi.fn()} />);
    expect(screen.getByText("안녕")).toBeInTheDocument();
    expect(screen.getByText("네, 시작합니다")).toBeInTheDocument();
  });

  it("renders QuestionForm when pendingQuestions is set", () => {
    mockStream({ pendingQuestions: QP });
    render(<BuildPanel projectId="p1" slug="todo-app" onClose={vi.fn()} />);
    expect(screen.getByText(/누구\?/)).toBeInTheDocument();
  });

  it("does not render QuestionForm when pendingQuestions is null", () => {
    mockStream();
    render(<BuildPanel projectId="p1" slug="todo-app" onClose={vi.fn()} />);
    expect(screen.queryByText(/누구\?/)).not.toBeInTheDocument();
  });

  it("중단 button is visible only while streaming, and calls interrupt", async () => {
    const interrupt = vi.fn().mockResolvedValue(undefined);
    mockStream({ streaming: true, interrupt });
    render(<BuildPanel projectId="p1" slug="todo-app" onClose={vi.fn()} />);

    const btn = screen.getByRole("button", { name: "중단" });
    expect(btn).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(btn);
    expect(interrupt).toHaveBeenCalledTimes(1);
  });

  it("중단 button is hidden while not streaming", () => {
    mockStream({ streaming: false });
    render(<BuildPanel projectId="p1" slug="todo-app" onClose={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "중단" })).not.toBeInTheDocument();
  });

  it("완료 calls closeSession then onClose", async () => {
    mockStream();
    const onClose = vi.fn();
    render(<BuildPanel projectId="p1" slug="todo-app" onClose={onClose} />);

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "완료" }));

    expect(prototypesApi.closeSession).toHaveBeenCalledWith("p1", "todo-app");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("renders the changedPaths list (파일 변경 목록)", () => {
    mockStream({ changedPaths: ["prototype/src/App.tsx", "prototype/README.md"] });
    render(<BuildPanel projectId="p1" slug="todo-app" onClose={vi.fn()} />);
    expect(screen.getByText("파일 변경 목록")).toBeInTheDocument();
    expect(screen.getByText("prototype/src/App.tsx")).toBeInTheDocument();
    expect(screen.getByText("prototype/README.md")).toBeInTheDocument();
  });

  it("calls startBuild on mount only when autoStart is true", () => {
    const startBuild = vi.fn();
    mockStream({ startBuild });
    render(<BuildPanel projectId="p1" slug="todo-app" onClose={vi.fn()} autoStart />);
    expect(startBuild).toHaveBeenCalledTimes(1);
  });

  it("does not call startBuild on mount when autoStart is false/omitted", () => {
    const startBuild = vi.fn();
    mockStream({ startBuild });
    render(<BuildPanel projectId="p1" slug="todo-app" onClose={vi.fn()} />);
    expect(startBuild).not.toHaveBeenCalled();
  });

  it("submitting an answer calls submitAnswers with the form's values", async () => {
    const submitAnswers = vi.fn().mockResolvedValue(undefined);
    mockStream({ pendingQuestions: QP, submitAnswers });
    render(<BuildPanel projectId="p1" slug="todo-app" onClose={vi.fn()} />);

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /답변 제출/ }));
    expect(submitAnswers).toHaveBeenCalledTimes(1);
  });

  it("ChatInput is disabled while streaming", () => {
    mockStream({ streaming: true });
    render(<BuildPanel projectId="p1" slug="todo-app" onClose={vi.fn()} />);
    expect(screen.getByLabelText("채팅 메시지 입력")).toBeDisabled();
  });

  it("완료 선언 후에는 입력이 막히고 안내 문구가 보인다", () => {
    // buildComplete가 서면 streaming은 이미 false로 돌아온 뒤다(done이
    // build_complete 뒤를 잇는다) — 그런데도 세션은 유예 타이머로 곧 닫히므로
    // 입력을 계속 열어두면 오해를 부르는 404/연결 오류로 이어진다.
    mockStream({ streaming: false, buildComplete: { summary: "완성", remaining: "" } });
    render(<BuildPanel projectId="p1" slug="todo-app" onClose={vi.fn()} />);
    expect(screen.getByLabelText("채팅 메시지 입력")).toBeDisabled();
    expect(screen.getByText(/빌드 세션이 종료됐습니다/)).toBeInTheDocument();
  });

  it("완료 전에는 streaming이 false여도 입력이 열려 있고 안내 문구가 없다", () => {
    mockStream({ streaming: false, buildComplete: null });
    render(<BuildPanel projectId="p1" slug="todo-app" onClose={vi.fn()} />);
    expect(screen.getByLabelText("채팅 메시지 입력")).not.toBeDisabled();
    expect(screen.queryByText(/빌드 세션이 종료됐습니다/)).not.toBeInTheDocument();
  });

  it("splits the drawer into two halves that do not overflow it", () => {
    // The regression this guards: the chat was widened from basis-1/3 to
    // basis-1/2 while the aside kept basis-2/3, so the two panes claimed 7/6 of
    // the row. Flex does not shrink a `basis` pane that also has min-w-0 back
    // below its basis here, so the overflow lands on the aside and clips the
    // right edge of every long option label — the answer text runs off-screen
    // with no scrollbar to reveal it.
    mockStream({ pendingQuestions: QP });
    const { container } = render(
      <BuildPanel projectId="p1" slug="todo-app" onClose={vi.fn()} />,
    );

    const row = container.querySelector(".md\\:flex-row");
    expect(row).not.toBeNull();
    const panes = Array.from(row!.children) as HTMLElement[];
    expect(panes).toHaveLength(2);

    // Read the fractions off the classnames rather than asserting a literal
    // pair, so a future 40/60 split stays legal and only overflow fails.
    const fractions = panes.map((p) => {
      const m = p.className.match(/md:basis-(\d+)\/(\d+)/);
      expect(m, `pane has no md:basis-N/M: ${p.className}`).not.toBeNull();
      return Number(m![1]) / Number(m![2]);
    });
    expect(fractions.reduce((a, b) => a + b, 0)).toBeLessThanOrEqual(1);
  });

  describe("완료 카드", () => {
    it("완료 선언 후 요약과 남은 작업을 보여준다", () => {
      mockStream({
        buildComplete: { summary: "할 일 앱을 만들었다", remaining: "다크 모드" },
      });
      render(<BuildPanel projectId="proj-1" slug="todo-app" onClose={vi.fn()} />);

      expect(screen.getByText(/할 일 앱을 만들었다/)).toBeInTheDocument();
      expect(screen.getByText(/다크 모드/)).toBeInTheDocument();
    });

    it("남은 작업이 비어 있으면 그 줄을 그리지 않는다", () => {
      mockStream({ buildComplete: { summary: "완성", remaining: "" } });
      render(<BuildPanel projectId="proj-1" slug="todo-app" onClose={vi.fn()} />);

      expect(screen.queryByText("남은 작업")).not.toBeInTheDocument();
    });

    it("완료 전에는 카드를 그리지 않는다", () => {
      mockStream({ buildComplete: null });
      render(<BuildPanel projectId="proj-1" slug="todo-app" onClose={vi.fn()} />);

      expect(screen.queryByRole("button", { name: "호스팅 시작" })).not.toBeInTheDocument();
    });

    it("호스팅 시작이 startHost를 부르고 패널을 닫는다", async () => {
      const onClose = vi.fn();
      mockStream({ buildComplete: { summary: "완성", remaining: "" } });
      render(<BuildPanel projectId="proj-1" slug="todo-app" onClose={onClose} />);

      const user = userEvent.setup();
      await user.click(screen.getByRole("button", { name: "호스팅 시작" }));

      expect(vi.mocked(prototypesApi.startHost)).toHaveBeenCalledWith("proj-1", "todo-app");
      expect(onClose).toHaveBeenCalled();
    });

    it("호스팅이 실패하면 패널을 닫지 않고 오류를 보여준다", async () => {
      const onClose = vi.fn();
      vi.mocked(prototypesApi.startHost).mockRejectedValueOnce(new Error("npm error"));
      mockStream({ buildComplete: { summary: "완성", remaining: "" } });
      render(<BuildPanel projectId="proj-1" slug="todo-app" onClose={onClose} />);

      const user = userEvent.setup();
      await user.click(screen.getByRole("button", { name: "호스팅 시작" }));

      expect(onClose).not.toHaveBeenCalled();
      expect(screen.getByText(/호스팅을 시작하지 못했습니다/)).toBeInTheDocument();
    });

    it("개선 이어서 하기가 restartForImprovement를 부른다", async () => {
      const restart = vi.fn().mockResolvedValue(undefined);
      mockStream({
        buildComplete: { summary: "완성", remaining: "" },
        restartForImprovement: restart,
      });
      render(<BuildPanel projectId="proj-1" slug="todo-app" onClose={vi.fn()} />);

      const user = userEvent.setup();
      await user.click(screen.getByRole("button", { name: "개선 이어서 하기" }));

      expect(restart).toHaveBeenCalled();
    });

    it("개선 시작이 429면 상한 메시지를 보여주고 카드를 남긴다", async () => {
      // 동시 빌드 상한에 걸린 경우. 카드를 지우면 사용자는 완료 요약과 다른
      // 선택지(호스팅)를 모두 잃는다 — 재시도할 수 있게 남긴다.
      const restart = vi.fn().mockRejectedValueOnce(
        new ApiError(429, "다른 팀이 프로토타입을 빌드하고 있습니다 — 잠시 후 다시 시도해 주세요"));
      mockStream({
        buildComplete: { summary: "완성", remaining: "" },
        restartForImprovement: restart,
      });
      render(<BuildPanel projectId="proj-1" slug="todo-app" onClose={vi.fn()} />);

      const user = userEvent.setup();
      await user.click(screen.getByRole("button", { name: "개선 이어서 하기" }));

      expect(screen.getByText(/다른 팀이 프로토타입을 빌드하고 있습니다/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "호스팅 시작" })).toBeInTheDocument();
    });

    it("완료 후 닫기는 세션이 이미 닫혀 404여도 패널을 닫는다", async () => {
      const onClose = vi.fn();
      vi.mocked(prototypesApi.closeSession).mockRejectedValueOnce(new ApiError(404, "no build session"));
      mockStream({ buildComplete: { summary: "완성", remaining: "" } });
      render(<BuildPanel projectId="proj-1" slug="todo-app" onClose={onClose} />);

      const user = userEvent.setup();
      await user.click(screen.getByRole("button", { name: "닫기" }));

      expect(onClose).toHaveBeenCalled();
    });
  });
});
