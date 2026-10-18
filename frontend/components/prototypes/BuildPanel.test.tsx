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
    agents: [],
    pendingQuestions: null,
    buildComplete: null,
    changedPaths: [],
    historyLoading: false,
    context: null,
    startBuild: vi.fn(),
    restoreHistory: vi.fn().mockResolvedValue(undefined),
    send: vi.fn(),
    submitAnswers: vi.fn().mockResolvedValue(undefined),
    interrupt: vi.fn().mockResolvedValue(undefined),
    restartForImprovement: vi.fn().mockResolvedValue(undefined),
    resume: vi.fn().mockResolvedValue(undefined),
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

  it("중단 버튼은 하나뿐이다 — 입력창 자리로 통일", async () => {
    // It used to be in the header. If it were not removed when added to the input box, there would be two
    // buttons for the same function on one screen, and the facilitator would have to point out which one every time.
    const interrupt = vi.fn().mockResolvedValue(undefined);
    mockStream({ streaming: true, interrupt });
    render(<BuildPanel projectId="p1" slug="todo-app" onClose={vi.fn()} />);

    expect(screen.getAllByRole("button", { name: "중단" })).toHaveLength(1);
  });

  it("빌드가 끝난 뒤에는 중단 버튼이 없다", () => {
    // The input box's disabled is `streaming || buildComplete !== null`, so it stays true even after
    // completion. Deciding the button by that value would show ■ when there is no turn to stop —
    // which is why interrupting is passed to ChatInput separately.
    mockStream({
      streaming: false,
      buildComplete: { summary: "할 일 앱", remaining: "" },
    });
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
    const resume = vi.fn().mockResolvedValue(undefined);
    const restoreHistory = vi.fn().mockResolvedValue(undefined);
    mockStream({ startBuild, resume, restoreHistory });
    render(<BuildPanel projectId="p1" slug="todo-app" onClose={vi.fn()} autoStart />);
    expect(startBuild).toHaveBeenCalledTimes(1);
    expect(resume).not.toHaveBeenCalled();
    // A newly opened session also lays out past conversations (previous sessions) first. resume lays them out itself.
    expect(restoreHistory).toHaveBeenCalledTimes(1);
  });

  it("does not ask what to change while the past conversation is still loading", () => {
    // Prevents the flicker of the hint appearing first and disappearing when the history arrives.
    mockStream({ historyLoading: true });
    render(<BuildPanel projectId="p1" slug="todo-app" onClose={vi.fn()} />);
    expect(screen.queryByText("무엇을 고칠지 알려주세요.")).toBeNull();
  });

  it("resumes the already-open session instead of starting when autoStart is false/omitted", () => {
    // Refresh/reopen: the session is alive and a turn may be running — instead of an empty panel, it
    // restores that session's conversation and attaches to the running turn.
    const startBuild = vi.fn();
    const resume = vi.fn().mockResolvedValue(undefined);
    mockStream({ startBuild, resume });
    render(<BuildPanel projectId="p1" slug="todo-app" onClose={vi.fn()} />);
    expect(startBuild).not.toHaveBeenCalled();
    expect(resume).toHaveBeenCalledTimes(1);
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
    // By the time buildComplete is set, streaming has already returned to false (done
    // follows build_complete) — yet the session closes soon on the grace timer, so
    // keeping the input open leads to misleading 404/connection errors.
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
    it("파일 변경 목록 **아래**에 온다 — 위에 있으면 스크롤을 되올려야 한다", () => {
      // User report: the moment the build finished, the card appeared above the scroll position, so
      // a user who had been scrolling down watching files pile up had to scroll back up to see the
      // completion. A single aside is the scroll container, so the order is the problem itself — pin it
      // by DOM order (the cause was order, not classes or coordinates).
      mockStream({
        streaming: false,
        buildComplete: { summary: "할 일 앱", remaining: "" },
        changedPaths: ["prototype/app/page.tsx", "prototype/lib/api.ts"],
      });
      render(<BuildPanel projectId="proj-1" slug="todo-app" onClose={vi.fn()} />);

      const files = screen.getByText("파일 변경 목록");
      const complete = screen.getByText("빌드 완료");
      expect(
        files.compareDocumentPosition(complete) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    });

    it("완료 선언 후 요약과 남은 작업을 보여준다", () => {
      mockStream({
        buildComplete: { summary: "할 일 앱을 만들었다", remaining: "다크 모드" },
      });
      render(<BuildPanel projectId="proj-1" slug="todo-app" onClose={vi.fn()} />);

      expect(screen.getByText(/할 일 앱을 만들었다/)).toBeInTheDocument();
      expect(screen.getByText(/다크 모드/)).toBeInTheDocument();
    });

    it("요약·남은 작업을 마크다운으로 렌더한다 — 원문 문법이 화면에 나오지 않는다", () => {
      // Regression: it was drawn as whitespace-pre-wrap text, so `- `, `**`, and backticks showed
      // verbatim. This text is the build_complete tool's argument, and the model writes lists and
      // emphasis (the tool schema does not prevent it). The same agent's prose in the left timeline
      // is already rendered as markdown, so the convention diverged within one screen.
      mockStream({
        buildComplete: {
          summary: "**할 일 앱**을 만들었다\n\n- 목록 추가\n- `localStorage` 저장",
          remaining: "- **다크 모드**",
        },
      });
      const { container } = render(
        <BuildPanel projectId="proj-1" slug="todo-app" onClose={vi.fn()} />,
      );

      // Emphasis, lists, and inline code exist as elements.
      expect(container.querySelector("strong")).toHaveTextContent("할 일 앱");
      expect(container.querySelectorAll("li")).toHaveLength(3);   // 2 summary + 1 remaining work
      expect(container.querySelector("code")).toHaveTextContent("localStorage");
      // No markdown syntax characters remain in the screen text.
      expect(screen.queryByText(/\*\*할 일 앱\*\*/)).toBeNull();
      expect(screen.queryByText(/`localStorage`/)).toBeNull();
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

    it("호스팅 시작의 502는 로그 꼬리를 싣지 않고 실패 문구를 보여준다", async () => {
      // The 502's detail is the tail of the npm log (start_host in backend routes/prototypes).
      vi.mocked(prototypesApi.startHost).mockRejectedValueOnce(
        new ApiError(502, "npm ERR! code ELIFECYCLE\nnpm ERR! errno 1"));
      mockStream({ buildComplete: { summary: "완성", remaining: "" } });
      render(<BuildPanel projectId="proj-1" slug="todo-app" onClose={vi.fn()} />);

      const user = userEvent.setup();
      await user.click(screen.getByRole("button", { name: "호스팅 시작" }));

      expect(screen.getByText(/호스팅을 시작하지 못했습니다/)).toBeInTheDocument();
      expect(screen.queryByText(/ELIFECYCLE/)).not.toBeInTheDocument();
    });

    it("호스팅 시작이 열린 세션에 막히면 무엇을 누를지 보여준다", async () => {
      vi.mocked(prototypesApi.startHost).mockRejectedValueOnce(
        new ApiError(409, "build_session_active"));
      mockStream({ buildComplete: { summary: "완성", remaining: "" } });
      render(<BuildPanel projectId="proj-1" slug="todo-app" onClose={vi.fn()} />);

      const user = userEvent.setup();
      await user.click(screen.getByRole("button", { name: "호스팅 시작" }));

      expect(screen.getByText(/빌드 패널에서 “완료”를 눌러/)).toBeInTheDocument();
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
      // The case of hitting the concurrent build limit. Clearing the card would make the user lose
      // both the completion summary and the other option (hosting) — keep it so they can retry.
      const restart = vi.fn().mockRejectedValueOnce(new ApiError(429, "build_slots_busy"));
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

    it("streaming 중에는 카드는 보이지만 세 버튼이 모두 비활성화되고 안내 문구가 보인다", () => {
      // build_complete is an event that arrives before done, so even after the card appears there is a
      // real window in which streaming stays true while the agent sends its wrap-up text
      // (see the comment on usePrototypeStream.restartForImprovement). If a button is pressed in that
      // window, the still-open stream and the new action get tangled — so the card itself keeps
      // rendering so the summary can be read right away, while only the buttons are blocked.
      mockStream({
        streaming: true,
        buildComplete: { summary: "완성", remaining: "" },
      });
      render(<BuildPanel projectId="proj-1" slug="todo-app" onClose={vi.fn()} />);

      expect(screen.getByText(/완성/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "호스팅 시작" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "개선 이어서 하기" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "닫기" })).toBeDisabled();
      expect(screen.getByText(/빌드를 마무리하고 있어요/)).toBeInTheDocument();
    });

    it("streaming이 끝난 뒤에는 세 버튼이 모두 활성화되고 안내 문구가 없다", () => {
      // A guard protecting the normal path (when done has already passed by the time the card appears) —
      // blocking unconditionally based only on streaming would keep the buttons grayed out all the time.
      mockStream({
        streaming: false,
        buildComplete: { summary: "완성", remaining: "" },
      });
      render(<BuildPanel projectId="proj-1" slug="todo-app" onClose={vi.fn()} />);

      expect(screen.getByRole("button", { name: "호스팅 시작" })).not.toBeDisabled();
      expect(screen.getByRole("button", { name: "개선 이어서 하기" })).not.toBeDisabled();
      expect(screen.getByRole("button", { name: "닫기" })).not.toBeDisabled();
      expect(screen.queryByText(/빌드를 마무리하고 있어요/)).not.toBeInTheDocument();
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

describe("BuildPanel — 입력창 위 고정 진행 줄", () => {
  // Places **the same component in the same spot** as the workspace. Build turns pour out especially
  // many tool activities, so "only the last one" pays off greatly, and if the two screens diverged the
  // same bar would behave differently depending on the screen.
  it("빌드가 도는 동안 마지막 활동을 한 줄로 보여준다", () => {
    mockStream({
      streaming: true,
      items: [{
        id: "a1", role: "ai", text: "만들고 있습니다", trace: [],
        streaming: true, error: null,
        activity: { kind: "tool", tool: "Write", detail: null },
      }],
    });
    render(<BuildPanel projectId="proj-1" slug="todo-app" onClose={vi.fn()} />);
    expect(screen.getByTestId("live-what")).toHaveTextContent("문서를 작성하고 있어요");
  });

  it("도는 턴이 없으면 그리지 않는다", () => {
    mockStream({ streaming: false, items: [] });
    render(<BuildPanel projectId="proj-1" slug="todo-app" onClose={vi.fn()} />);
    expect(screen.queryByTestId("live-what")).not.toBeInTheDocument();
  });
});


// ---- In an edit session the user speaks first ----
//
// Auto-open first burned a round trip of the agent asking "what to improve", and while that
// question was up the input box was locked so the user could not even type their own request
// (rationale in backend prompts.handoff_prompt). So in built/running there is no auto-speaking;
// it opens as an empty panel, and that first message becomes the opening turn.

describe("BuildPanel — 빈 수정 세션", () => {
  it("무엇을 고칠지 물어보는 안내를 보여준다", () => {
    // A moment with nothing on screen is the price of this design. If it does not say what to do then,
    // it reads as "nothing happened".
    mockStream({ items: [], streaming: false });
    render(<BuildPanel projectId="p1" slug="todo-app" onClose={vi.fn()} />);
    expect(screen.getByText("무엇을 고칠지 알려주세요.")).toBeInTheDocument();
    expect(screen.getByText(/장바구니 버튼을 오른쪽 위로/)).toBeInTheDocument();
  });

  it("대화가 시작되면 안내가 사라진다", () => {
    mockStream({
      items: [{ id: "a", role: "ai", text: "고쳤습니다", trace: [], streaming: false, error: null }],
      streaming: false,
    });
    render(<BuildPanel projectId="p1" slug="todo-app" onClose={vi.fn()} />);
    expect(screen.queryByText("무엇을 고칠지 알려주세요.")).toBeNull();
  });

  it("턴이 도는 동안에는 안내를 띄우지 않는다", () => {
    // Auto-open (first build) starts streaming with items empty — "what to fix" would be the
    // wrong hint at that point.
    mockStream({ items: [], streaming: true });
    render(<BuildPanel projectId="p1" slug="todo-app" onClose={vi.fn()} />);
    expect(screen.queryByText("무엇을 고칠지 알려주세요.")).toBeNull();
  });
});
