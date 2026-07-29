// frontend/components/prototypes/BuildPanel.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BuildPanel } from "./BuildPanel";
import * as prototypeStream from "@/lib/usePrototypeStream";
import * as prototypesApi from "@/lib/api/prototypes";

vi.mock("@/lib/api/prototypes", async (orig) => ({
  ...(await orig<typeof import("@/lib/api/prototypes")>()),
  closeSession: vi.fn().mockResolvedValue(undefined),
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
    changedPaths: [],
    startBuild: vi.fn(),
    send: vi.fn(),
    submitAnswers: vi.fn().mockResolvedValue(undefined),
    interrupt: vi.fn().mockResolvedValue(undefined),
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
});
